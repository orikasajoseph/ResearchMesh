"""arXiv 核验的离线安全测试：合成字节、模拟解析器与 MockTransport，无额外依赖。"""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from arxiv_pdf import (ArxivPdfResolver, arxiv_id, inspect_pdf, PdfSourceError,
                       validate_pdf_enrichment, MAX_PDF)
from reader_adapter import ReaderAdapterError
from reader_batch_adapter import prepare_reader_batch
from tests.test_reader_batch_adapter import batch_task

ABS = "https://arxiv.org/abs/9999.12345"
PDF = "https://arxiv.org/pdf/9999.12345"
TITLE = "Synthetic Test Paper"


def pdf_bytes(title=TITLE):
    # 不是实际 PDF，明确只配合下方 FakePdfReader 验证控制流程。
    return ("%PDF-synthetic\nTEXT:" + title).encode("ascii")


class FakePdfReader:
    def __init__(self, stream):
        value = stream.read().decode("ascii")
        if "\nTEXT:" not in value:
            raise ValueError("synthetic corrupt fixture")
        text = value.split("\nTEXT:", 1)[1]
        self.is_encrypted = False
        self.pages = [SimpleNamespace(extract_text=lambda: text)]


class ReadTimeoutStream(httpx.AsyncByteStream):
    def __init__(self, request):
        self.request = request

    async def __aiter__(self):
        raise httpx.ReadTimeout("private streamed body and token", request=self.request)
        yield b""  # pragma: no cover - makes this an async generator

    async def aclose(self):
        pass


def fixtures():
    task = batch_task()
    task["input"]["papers"] = task["input"]["papers"][:1]
    task["constraints"] = {"maxPapers": 1}
    paper = task["input"]["papers"][0]
    paper.update(title=TITLE, pdfUrl=None)
    task, _ = prepare_reader_batch(task)
    result = {"items": [{"kind": "paper", "data": {**deepcopy(paper), "sourceUrls": [ABS]}}]}
    html = f'<meta name="citation_title" content="{TITLE}"><meta name="citation_pdf_url" content="{PDF}">'.encode()
    return task, result, html


class PdfResolverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        parser = patch.dict("sys.modules", {"pypdf": SimpleNamespace(PdfReader=FakePdfReader)})
        parser.start()
        self.addCleanup(parser.stop)

    async def resolve_with(self, handler, *, change=None):
        task, result, _ = fixtures()
        if change: change(task, result)
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                resolver = ArxivPdfResolver(directory, client=client, request_interval=0)
                return await resolver.resolve(task, result)

    async def test_verified_title_link_real_pdf_and_cache_reuse(self):
        task, result, html = fixtures()
        originals = deepcopy((task, result))
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=html if str(request.url) == ABS else pdf_bytes())
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                resolver = ArxivPdfResolver(directory, client=client, request_interval=0)
                resolved, events = await resolver.resolve(task, result)
                self.assertEqual(resolved["input"]["papers"][0]["pdfUrl"], PDF)
                self.assertEqual(events[0]["state"], "verified")
                self.assertEqual(events[0]["pages"], 1)
                self.assertEqual(await resolver.fetch_pdf(PDF), pdf_bytes())
                second = ArxivPdfResolver(directory, client=client, request_interval=0)
                resumed, reused = await second.resolve(task, result, previous=events)
                self.assertEqual(resumed, resolved)
                self.assertTrue(reused[0]["cacheReused"])
                self.assertFalse(reused[0]["networkAttempted"])
                self.assertEqual(len(requests), 2)
                self.assertTrue(all("authorization" not in r.headers and "x-api-key" not in r.headers for r in requests))
        self.assertEqual((task, result), originals)

    async def test_no_source_does_not_guess_or_request(self):
        def handler(request): self.fail("must not request an invented source")
        resolved, events = await self.resolve_with(handler, change=lambda t, r: r["items"][0]["data"].update(sourceUrls=[]))
        self.assertIsNone(resolved["input"]["papers"][0]["pdfUrl"])
        self.assertEqual(events[0]["reason"], "NO_KNOWN_ARXIV_SOURCE")

    async def test_tampered_cache_not_reused_or_silently_overwritten(self):
        task, result, html = fixtures()
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=html if str(request.url) == ABS else pdf_bytes())
        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                resolver = ArxivPdfResolver(directory, client=client, request_interval=0)
                _, events = await resolver.resolve(task, result)
                Path(events[0]["cachePath"]).write_bytes(b"corrupt cached file")
                second = ArxivPdfResolver(directory, client=client, request_interval=0)
                resolved, failed = await second.resolve(task, result, previous=events)
                self.assertIsNone(resolved["input"]["papers"][0]["pdfUrl"])
                self.assertEqual(failed[0]["reason"], "PDF_CACHE_CONFLICT")
                self.assertEqual(failed[0]["diagnostics"], {
                    "exceptionClass": "PdfSourceError", "stage": "pdf_cache", "attempt": 1,
                    "responseReceived": True, "httpStatus": 200})
                self.assertEqual(Path(events[0]["cachePath"]).read_bytes(), b"corrupt cached file")
                self.assertEqual(len(calls), 4)

    async def test_source_mismatch_rejected_before_pdf_request(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, content=b'<meta name="citation_title" content="Other Paper">')
        resolved, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "SOURCE_TITLE_MISMATCH")
        self.assertEqual(calls, [ABS])
        self.assertIsNone(resolved["input"]["papers"][0]["pdfUrl"])

    async def test_page_without_link_does_not_construct_pdf_url(self):
        def handler(request): return httpx.Response(200, content=f'<meta name="citation_title" content="{TITLE}">'.encode())
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "NO_VERIFIED_PDF_LINK")

    async def test_link_to_other_paper_not_accepted(self):
        _, _, html = fixtures()
        def handler(request): return httpx.Response(200, content=html.replace(b"/pdf/9999.12345", b"/pdf/9999.00001"))
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "NO_VERIFIED_PDF_LINK")

    async def test_html_masquerading_as_pdf_rejected(self):
        _, _, html = fixtures()
        def handler(request): return httpx.Response(200, content=html if str(request.url) == ABS else b"<html>not a PDF</html>")
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "INVALID_PDF")

    async def test_pdf_title_mismatch_rejected(self):
        _, _, html = fixtures()
        def handler(request): return httpx.Response(200, content=html if str(request.url) == ABS else pdf_bytes("Wrong Paper"))
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "PDF_TITLE_MISMATCH")

    async def test_external_redirect_blocked_before_following(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "UNSAFE_ARXIV_REDIRECT")
        self.assertEqual(calls, [ABS])

    async def test_http_error_safe_and_no_retry(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(429, content=b"private error body")
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "ARXIV_HTTP_429")
        self.assertNotIn("private error body", str(events))
        self.assertEqual(calls, [ABS])

    async def test_transport_error_has_allow_listed_diagnostics(self):
        def handler(request):
            raise httpx.ConnectTimeout("private host and token details", request=request)
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "ARXIV_NETWORK_UNAVAILABLE")
        self.assertEqual(events[0]["diagnostics"], {
            "exceptionClass": "ConnectTimeout", "stage": "abs_page", "attempt": 1,
            "responseReceived": False, "httpStatus": None})
        self.assertNotIn("private host and token details", str(events))

    async def test_pdf_body_transport_error_has_allow_listed_diagnostics(self):
        _, _, html = fixtures()
        def handler(request):
            if str(request.url) == ABS:
                return httpx.Response(200, content=html)
            raise httpx.ReadTimeout("private PDF endpoint and token", request=request)
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "ARXIV_NETWORK_UNAVAILABLE")
        self.assertEqual(events[0]["diagnostics"], {
            "exceptionClass": "ReadTimeout", "stage": "pdf_body", "attempt": 1,
            "responseReceived": False, "httpStatus": None})
        self.assertNotIn("private PDF endpoint and token", str(events))

    async def test_pdf_body_stream_timeout_preserves_received_status(self):
        _, _, html = fixtures()
        def handler(request):
            if str(request.url) == ABS:
                return httpx.Response(200, content=html)
            return httpx.Response(200, stream=ReadTimeoutStream(request))
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "ARXIV_NETWORK_UNAVAILABLE")
        self.assertEqual(events[0]["diagnostics"], {
            "exceptionClass": "ReadTimeout", "stage": "pdf_body", "attempt": 1,
            "responseReceived": True, "httpStatus": 200})
        self.assertNotIn("private streamed body and token", str(events))

    async def test_http_status_diagnostics_for_404_429_and_5xx(self):
        for status in (404, 429, 503):
            with self.subTest(status=status):
                def handler(request, status=status):
                    return httpx.Response(status, content=b"private response body")
                _, events = await self.resolve_with(handler)
                self.assertEqual(events[0]["reason"], f"ARXIV_HTTP_{status}")
                self.assertEqual(events[0]["diagnostics"], {
                    "exceptionClass": "PdfSourceError", "stage": "abs_page", "attempt": 1,
                    "responseReceived": True, "httpStatus": status})
                self.assertNotIn("private response body", str(events))

    async def test_redirect_attempt_is_recorded_without_location(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx.Response(302, headers={"location": "/abs/9999.12345"})
            return httpx.Response(503, content=b"private")
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["diagnostics"]["attempt"], 2)
        self.assertEqual(events[0]["diagnostics"]["httpStatus"], 503)
        self.assertNotIn("location", str(events[0]["diagnostics"]))

    async def test_oversized_response_rejected(self):
        _, _, html = fixtures()
        def handler(request):
            if str(request.url) == ABS: return httpx.Response(200, content=html)
            return httpx.Response(200, content=b"%PDF-", headers={"content-length": str(MAX_PDF + 1)})
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "SOURCE_TOO_LARGE")

    async def test_pdf_validation_failure_records_stage(self):
        _, _, html = fixtures()
        def handler(request):
            return httpx.Response(200, content=html if str(request.url) == ABS else b"not a pdf")
        _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "INVALID_PDF")
        self.assertEqual(events[0]["diagnostics"], {
            "exceptionClass": "PdfSourceError", "stage": "pdf_validation", "attempt": 1,
            "responseReceived": True, "httpStatus": 200})

    async def test_cache_oserror_records_concrete_class_without_message(self):
        _, _, html = fixtures()
        def handler(request):
            return httpx.Response(200, content=html if str(request.url) == ABS else pdf_bytes())
        with patch("arxiv_pdf.Path.mkdir",
                   side_effect=PermissionError("private cache path and credential")):
            _, events = await self.resolve_with(handler)
        self.assertEqual(events[0]["reason"], "PDF_CACHE_UNAVAILABLE")
        self.assertEqual(events[0]["diagnostics"], {
            "exceptionClass": "PermissionError", "stage": "pdf_cache", "attempt": 1,
            "responseReceived": True, "httpStatus": 200})
        self.assertNotIn("private cache path and credential", str(events))

    async def test_existing_pdf_not_replaced_or_requested(self):
        def handler(request): self.fail("existing PDF not resolved by this module")
        resolved, events = await self.resolve_with(handler, change=lambda t, r: t["input"]["papers"][0].update(pdfUrl="https://example.invalid/existing.pdf"))
        self.assertEqual(events, [])
        self.assertEqual(resolved["input"]["papers"][0]["pdfUrl"], "https://example.invalid/existing.pdf")

    def test_untrusted_hosts_and_credentials_rejected(self):
        for url in ["https://arxiv.org.evil/abs/9999.12345", "https://arxiv.org@evil/abs/9999.12345",
                    "https://user:password@arxiv.org/abs/9999.12345", "https://arxiv.org:invalid/abs/9999.12345",
                    "https://arxiv.org/abs/9999.12345?redirect=private"]:
            self.assertIsNone(arxiv_id(url, "abs"))
        self.assertEqual(arxiv_id("https://arxiv.org/abs/hep-th/9901001v2", "abs"), "hep-th/9901001v2")

    def test_no_other_task_changes_permitted(self):
        before, _, _ = fixtures()
        for mutate in [lambda x: x.update(goal="new goal"),
                       lambda x: x["input"]["papers"][0].update(title="Wrong Paper"),
                       lambda x: x["input"]["papers"][0].update(pdfUrl="http://127.0.0.1/private")]:
            after = deepcopy(before)
            mutate(after)
            with self.assertRaises(ReaderAdapterError): validate_pdf_enrichment(before, after)

    def test_invalid_pdf_and_empty_text_rejected(self):
        with self.assertRaises(PdfSourceError): inspect_pdf(b"%PDF-corrupt", TITLE)
        with self.assertRaisesRegex(PdfSourceError, "PDF_TITLE_MISMATCH"): inspect_pdf(pdf_bytes(""), TITLE)


if __name__ == "__main__":
    unittest.main()
