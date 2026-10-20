"""Offline resource discovery and contract checks; all HTTP is mocked."""
import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import httpx

from agents.literature.researchmesh_literature.config import LiteratureAgentConfig
from agents.literature.researchmesh_literature.provider import SemanticScholarProvider
from agents.literature.researchmesh_literature.resource_resolver import ResourceResolver
from agents.literature.researchmesh_literature.schemas import LiteratureSearchRequest, PaperRecord, PaperRecordsResponse
from literature_adapter import normalize_response
from input_binding import bind_task_input
from orchestrator import make_task_payload


TITLE = "QLoRA: Efficient Finetuning of Quantized LLMs"
ARXIV = "2305.14314"
PDF = f"https://arxiv.org/pdf/{ARXIV}"
REPO = "https://github.com/artidoro/qlora"
ABS = f"https://arxiv.org/abs/{ARXIV}"


def paper(*, pdf=None, repos=None, title=TITLE):
    return PaperRecord(paperId="qlora-paper", title=title,
                       url="https://www.semanticscholar.org/paper/qlora-paper",
                       pdfUrl=pdf, repositoryUrls=repos or [])


def raw(arxiv=ARXIV):
    return {"externalIds": {"ArXiv": arxiv} if arxiv else {}}


def reply(status=200, content=b"", *, json_data=None):
    return httpx.Response(status, content=content if json_data is None else json.dumps(json_data).encode())


def readme(text):
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    def test_resource_proxy_setting_is_independent(self):
        with patch.dict(os.environ, {"LITERATURE_RESOURCE_TRUST_ENV": "0",
                                     "LITERATURE_OPENALEX_TRUST_ENV": "1"}):
            config = LiteratureAgentConfig.from_env()
        self.assertFalse(config.resource_trust_env)
        self.assertTrue(config.openalex_trust_env)

    async def test_direct_setting_reaches_only_resource_http_client(self):
        fake_client = SimpleNamespace(aclose=AsyncMock())
        with patch("agents.literature.researchmesh_literature.resource_resolver.httpx.AsyncClient",
                   return_value=fake_client) as constructor:
            async with ResourceResolver(trust_env=False):
                pass
        self.assertFalse(constructor.call_args.kwargs["trust_env"])
        fake_client.aclose.assert_awaited_once()

    async def resolve(self, routes, value=None, source=None, *, budget=12):
        calls = []
        def handler(request):
            url = str(request.url)
            calls.append(url)
            response = routes.get(request.url.path, routes.get(url))
            if isinstance(response, Exception):
                raise response
            return response or reply(404)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with ResourceResolver(max_requests=budget, client=client) as resolver:
                result, report = await resolver.resolve(value or paper(), source or raw())
                return result, report, calls, resolver

    async def test_existing_pdf_and_verified_direct_repository(self):
        routes = {
            f"/pdf/{ARXIV}": reply(content=b"%PDF-1.7 mock"),
            "/repos/artidoro/qlora/readme": reply(json_data=readme(
                f"# {TITLE}\nThis repo supports the paper arXiv:{ARXIV}.")),
        }
        result, report, calls, _ = await self.resolve(routes, paper(pdf=PDF, repos=[REPO]))
        self.assertEqual(result.pdfUrl, PDF)
        self.assertEqual(result.repositoryUrls, [REPO])
        self.assertEqual(report["pdf"]["source"], "semantic-scholar.openAccessPdf")
        self.assertEqual(report["repository"]["status"], "verified")
        self.assertEqual(len(calls), 2)

    async def test_request_diagnostics_are_bounded_and_do_not_store_query(self):
        routes = {
            f"/pdf/{ARXIV}": reply(content=b"%PDF-1.7 mock"),
            "/abs/2305.14314": reply(content=f'<meta name="citation_title" content="{TITLE}">'.encode()),
            "/search/repositories": reply(429),
        }
        result, report, calls, resolver = await self.resolve(routes, paper(pdf=PDF))
        self.assertEqual(result.pdfUrl, PDF)
        self.assertEqual(result.repositoryUrls, [])
        self.assertEqual(report["repository"]["reason"], "RATE_LIMITED")
        self.assertEqual(len(calls), len(resolver.request_events))
        self.assertEqual([event["purpose"] for event in resolver.request_events],
                         ["verify_pdf", "find_repository_on_paper_page", "search_repository"])
        search = resolver.request_events[-1]
        self.assertEqual(search["host"], "api.github.com")
        self.assertEqual(search["pathId"], "github.search")
        self.assertEqual(search["status"], 429)
        self.assertEqual(search["remainingBudget"], 9)
        self.assertNotIn("QLoRA", json.dumps(resolver.request_events))
        self.assertNotIn("?", json.dumps(resolver.request_events))

    async def test_timeout_diagnostic_preserves_exception_class(self):
        result, report, _, resolver = await self.resolve(
            {f"/pdf/{ARXIV}": reply(content=b"%PDF-1.7 mock"),
             "/search/repositories": httpx.ReadTimeout("secret request URL")},
            paper(pdf=PDF), raw())
        self.assertEqual(result.pdfUrl, PDF)
        self.assertEqual(result.repositoryUrls, [])
        self.assertEqual(report["repository"]["reason"], "TIMEOUT")
        search = resolver.request_events[-1]
        self.assertEqual(search["errorType"], "ReadTimeout")
        self.assertIsNone(search["status"])
        self.assertNotIn("secret", json.dumps(search))

    async def test_arxiv_pdf_and_readme_identity_discovery(self):
        routes = {
            f"/abs/{ARXIV}": reply(206, content=f'<meta name="citation_title" content="{TITLE}">'.encode()),
            f"/pdf/{ARXIV}": reply(206, content=b"%PDF-1.7 mock"),
            "/search/repositories": reply(206, json_data={"items": [
                {"html_url": REPO, "fork": False}]}),
            "/repos/artidoro/qlora/readme": reply(json_data=readme(
                f"# {TITLE}\nThis repo supports the paper arXiv:{ARXIV}.")),
        }
        result, report, calls, _ = await self.resolve(routes)
        self.assertEqual(result.pdfUrl, PDF)
        self.assertEqual(result.repositoryUrls, [REPO])
        self.assertEqual(report["pdf"]["source"], "arxiv.externalIds+title")
        self.assertEqual(report["repository"]["source"], "github.search+README")
        self.assertEqual(len(calls), 4)

    async def test_paper_page_explicit_repository_precedes_search(self):
        routes = {
            f"/abs/{ARXIV}": reply(content=(f'<meta name="citation_title" content="{TITLE}">'
                                             f'<a href="{REPO}">Code</a>').encode()),
            f"/pdf/{ARXIV}": reply(content=b"%PDF-1.7 mock"),
            "/repos/artidoro/qlora/readme": reply(json_data=readme(
                f"# {TITLE}\nThis repo supports the paper arXiv:{ARXIV}.")),
        }
        result, report, calls, _ = await self.resolve(routes)
        self.assertEqual(result.repositoryUrls, [REPO])
        self.assertEqual(report["repository"]["source"], "arxiv.paper-page")
        self.assertEqual(report["repository"]["discoveryUrl"], ABS)
        self.assertFalse(any("search/repositories" in url for url in calls))

    async def test_missing_pdf_and_repository_remain_missing(self):
        result, report, calls, _ = await self.resolve({"/search/repositories": reply(json_data={"items": []})})
        self.assertIsNone(result.pdfUrl)
        self.assertEqual(result.repositoryUrls, [])
        self.assertEqual(report["pdf"]["status"], "missing")
        self.assertEqual(report["repository"]["reason"], "NO_VERIFIED_REPOSITORY")
        self.assertEqual(len(calls), 2)

    async def test_same_name_unrelated_repository_rejected(self):
        routes = {"/search/repositories": reply(json_data={"items": [
            {"html_url": REPO, "fork": False}]}),
            "/repos/artidoro/qlora/readme": reply(json_data=readme(
                f"# {TITLE}\nUnofficial implementation of arXiv:9999.99999."))}
        result, report, _, _ = await self.resolve(routes)
        self.assertEqual(result.repositoryUrls, [])
        self.assertEqual(report["repository"]["reason"], "GITHUB_README_IDENTITY_MISMATCH")

    async def test_same_identifier_unofficial_repository_rejected(self):
        routes = {"/search/repositories": reply(json_data={"items": [
            {"html_url": REPO, "fork": False}]}),
            "/repos/artidoro/qlora/readme": reply(json_data=readme(
                f"# {TITLE}\nUnofficial reimplementation of arXiv:{ARXIV}."))}
        result, _, _, _ = await self.resolve(routes)
        self.assertEqual(result.repositoryUrls, [])

    async def test_inaccessible_url_timeout_and_rate_limit(self):
        for pdf_response, search_response, pdf_reason, repo_reason in (
            (reply(404), reply(404), "HTTP_404", "HTTP_404"),
            (httpx.ReadTimeout("offline"), httpx.ReadTimeout("offline"), "TIMEOUT", "TIMEOUT"),
            (reply(429), reply(429), "RATE_LIMITED", "RATE_LIMITED"),
        ):
            routes = {f"/pdf/{ARXIV}": pdf_response, "/search/repositories": search_response}
            result, report, _, _ = await self.resolve(routes, paper(pdf=PDF), raw(None))
            self.assertIsNone(result.pdfUrl)
            self.assertEqual(result.repositoryUrls, [])
            self.assertEqual(report["pdf"]["reason"], pdf_reason)
            self.assertEqual(report["repository"]["reason"],
                             "RATE_LIMITED" if pdf_reason == "RATE_LIMITED" else "PAPER_IDENTIFIER_MISSING")
            # The repository search transport failure is checked with an identifier.
            routes = {"/search/repositories": search_response}
            def handler(request):
                response = routes.get(request.url.path, reply(404))
                if isinstance(response, Exception):
                    raise response
                return response
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                async with ResourceResolver(client=client) as resolver:
                    self.assertIsNone(await resolver._find_repository(paper(), [ARXIV], []))
                    self.assertEqual("RATE_LIMITED" if resolver.rate_limited else resolver.last_repository_reason,
                                     repo_reason)

    async def test_arxiv_title_mismatch_cannot_create_pdf(self):
        routes = {f"/abs/{ARXIV}": reply(content=b'<meta name="citation_title" content="Other paper">'),
                  "/search/repositories": reply(json_data={"items": []})}
        result, report, _, _ = await self.resolve(routes)
        self.assertIsNone(result.pdfUrl)
        self.assertEqual(report["pdf"]["reason"], "ARXIV_TITLE_MISMATCH")

    async def test_request_budget_stops_discovery(self):
        result, report, calls, _ = await self.resolve({}, budget=1)
        self.assertIsNone(result.pdfUrl)
        self.assertEqual(result.repositoryUrls, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["repository"]["reason"], "REQUEST_BUDGET_EXHAUSTED")

    async def test_openalex_pdf_requires_exact_doi_and_title(self):
        doi = "10.1234/qlora"
        value = paper()
        value.doi = doi
        work = {"doi": "https://doi.org/" + doi, "display_name": TITLE,
                "best_oa_location": {"is_oa": True, "pdf_url": "https://publisher.example/paper.pdf"}}
        routes = {"/works/https://doi.org/10.1234/qlora": reply(json_data=work),
                  "/paper.pdf": reply(content=b"%PDF-1.7 mock"),
                  "/search/repositories": reply(json_data={"items": []})}
        result, report, _, _ = await self.resolve(routes, value, raw(None))
        self.assertEqual(result.pdfUrl, "https://publisher.example/paper.pdf")
        self.assertEqual(report["pdf"]["source"], "openalex.best_oa_location")
        work["display_name"] = "Different paper"
        routes["/works/https://doi.org/10.1234/qlora"] = reply(json_data=work)
        result, _, _, _ = await self.resolve(routes, value, raw(None))
        self.assertIsNone(result.pdfUrl)

    async def test_optional_provider_and_paper_records_contract(self):
        item = {"paperId": "qlora-paper", "title": TITLE,
                "externalIds": {"ArXiv": ARXIV}, "url": "https://www.semanticscholar.org/paper/qlora-paper",
                "openAccessPdf": {"url": PDF}}
        client = SimpleNamespace(search_paper=AsyncMock(return_value=SimpleNamespace(
            items=[SimpleNamespace(raw_data=item)], total=1)))
        provider = SemanticScholarProvider(config=LiteratureAgentConfig(rate_limit_per_second=1000), client=client)
        searched = await provider.search(LiteratureSearchRequest(requestId="x", query="QLoRA"))
        self.assertFalse(provider.resource_diagnostics()["resourceResolution"]["enabled"])
        response = PaperRecordsResponse(requestId="x", status="success", papers=searched.papers,
                                        total=1, metrics={"durationMs": 1, "attempts": 1, "cacheHit": False})
        payload = make_task_payload({"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
                                     "goal": "search", "input": {"query": "QLoRA"}, "constraints": {},
                                     "outputSchema": "paper_records.v1", "requiredSkill": "literature.search",
                                     "dependsOn": []})
        outcome = normalize_response(payload, {"schemaVersion": "literature_search.v0.1",
            "requestId": "x", "query": "QLoRA", "filters": {"year": None}, "limit": 5},
            response.model_dump(mode="json"))
        self.assertEqual(outcome["result"]["items"][0]["data"]["pdfUrl"], PDF)
        self.assertEqual(response.schemaVersion, "paper_records.v1")
        self.assertEqual(set(response.papers[0].model_dump()) - set(PaperRecord.model_fields), set())

    async def test_enabled_provider_maps_verified_urls_without_schema_change(self):
        item = {"paperId": "qlora-paper", "title": TITLE,
                "externalIds": {"ArXiv": ARXIV}, "url": "https://www.semanticscholar.org/paper/qlora-paper",
                "openAccessPdf": {"url": PDF}}
        sdk = SimpleNamespace(search_paper=AsyncMock(return_value=SimpleNamespace(
            items=[SimpleNamespace(raw_data=item)], total=1)))
        routes = {f"/pdf/{ARXIV}": reply(content=b"%PDF-1.7 mock"),
                  "/search/repositories": reply(json_data={"items": [{"html_url": REPO, "fork": False}]}),
                  "/repos/artidoro/qlora/readme": reply(json_data=readme(
                      f"# {TITLE}\nThis repo supports the paper arXiv:{ARXIV}."))}
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: routes.get(request.url.path, reply(404)))) as resource_http:
            with patch("agents.literature.researchmesh_literature.provider.ResourceResolver",
                       side_effect=lambda **kwargs: ResourceResolver(client=resource_http,
                                                                       max_requests=kwargs["max_requests"])):
                provider = SemanticScholarProvider(config=LiteratureAgentConfig(
                    rate_limit_per_second=1000, resolve_resources=True), client=sdk)
                searched = await provider.search(LiteratureSearchRequest(requestId="x", query="QLoRA"))
                cached = await provider.search(LiteratureSearchRequest(requestId="x", query="QLoRA"))
        self.assertIn(REPO, searched.papers[0].repositoryUrls)
        self.assertIn(PDF, searched.papers[0].repositoryUrls)
        self.assertEqual(searched.papers[0].pdfUrl, PDF)
        self.assertTrue(cached.cache_hit)
        self.assertEqual(provider.resource_diagnostics()["resourceResolution"]["httpRequests"], 4)
        self.assertEqual(len(provider.resource_diagnostics()["resourceResolution"]["requestEvents"]), 4)
        sdk.search_paper.assert_awaited_once()
        response = PaperRecordsResponse(requestId="x", status="success", papers=searched.papers,
                                        total=1, metrics={"durationMs": 1, "attempts": 1, "cacheHit": False})
        self.assertEqual(response.schemaVersion, "paper_records.v1")
        self.assertEqual(set(response.papers[0].model_dump()), set(PaperRecord.model_fields))
        s1 = {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
              "goal": "search", "input": {"query": "QLoRA"}, "constraints": {},
              "outputSchema": "paper_records.v1", "requiredSkill": "literature.search", "dependsOn": []}
        normalized = normalize_response(make_task_payload(s1),
            {"schemaVersion": "literature_search.v0.1", "requestId": "x", "query": "QLoRA",
             "filters": {"year": None}, "limit": 5}, response.model_dump(mode="json"))["result"]
        self.assertIn(PDF, normalized["items"][0]["data"]["sourceUrls"])
        self.assertEqual(normalized["items"][0]["data"]["repositoryUrls"], [REPO])
        s2 = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
              "goal": "read", "input": {"papers": []}, "constraints": {"maxPapers": 1},
              "outputSchema": "paper_notes.v1", "requiredSkill": "paper.analyze", "dependsOn": ["S1"]}
        s3 = {"schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
              "goal": "inspect", "input": {"papers": [], "paperNotes": [], "repositoryUrls": []},
              "constraints": {"maxRepositories": 1, "allowRepositorySearch": False,
                              "executeCode": False}, "outputSchema": "code_records.v1",
              "requiredSkill": "repository.analyze", "dependsOn": ["S1"]}
        self.assertEqual(bind_task_input(s2, {"S1": normalized})["input"]["papers"][0]["pdfUrl"], PDF)
        self.assertEqual(bind_task_input(s3, {"S1": normalized})["input"]["repositoryUrls"], [REPO])


if __name__ == "__main__":
    unittest.main()
