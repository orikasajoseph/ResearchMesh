"""仅从已知 arXiv 来源核验全文；不搜索标题、不调用模型、不改业务协议。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx

from input_binding import paper_ref
from reader_adapter import ReaderAdapterError

MAX_PDF = 50 * 1024 * 1024
ARXIV_ID = r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z][a-z.-]*/[0-9]{7})(?:v[1-9][0-9]*)?"


class PdfSourceError(ValueError):
    pass


def arxiv_id(url, kind):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"arxiv.org", "www.arxiv.org"}
            or parsed.username or parsed.password or port not in {None, 80, 443}
            or parsed.query or parsed.fragment):
        return None
    match = re.fullmatch(rf"/{kind}/({ARXIV_ID})(?:\.pdf)?", parsed.path)
    return match.group(1) if match else None


def normalize_title(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value or "").casefold() if c.isalnum())


class ArxivPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = None
        self.links = []
    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "meta" and fields.get("name", "").lower() == "citation_title":
            self.title = fields.get("content")
        if tag == "meta" and fields.get("name", "").lower() == "citation_pdf_url":
            self.links.append(fields.get("content", ""))
        if tag == "a" and fields.get("href"):
            self.links.append(fields["href"])


def inspect_pdf(content, title):
    from pypdf import PdfReader
    if not content.lstrip().startswith(b"%PDF-") or len(content) > MAX_PDF:
        raise PdfSourceError("INVALID_PDF")
    try:
        pdf = PdfReader(BytesIO(content))
        if pdf.is_encrypted or not pdf.pages:
            raise PdfSourceError("PDF_UNREADABLE")
        text = pdf.pages[0].extract_text() or ""
        if not normalize_title(title) or normalize_title(title) not in normalize_title(text):
            raise PdfSourceError("PDF_TITLE_MISMATCH")
        return len(pdf.pages)
    except PdfSourceError:
        raise
    except Exception:
        raise PdfSourceError("PDF_UNREADABLE") from None


def validate_pdf_enrichment(before, after):
    """只允许把原来为空的 PDF 字段补为 arXiv URL，其余任务与论文信息不变。"""
    expected = deepcopy(after)
    try:
        original = before["input"]["papers"]
        updated = expected["input"]["papers"]
        if len(original) != len(updated):
            raise ValueError()
        for old, new in zip(original, updated):
            if old["pdfUrl"] != new["pdfUrl"]:
                if old["pdfUrl"] is not None or not arxiv_id(new["pdfUrl"], "pdf"):
                    raise ValueError()
                new["pdfUrl"] = None
        if expected != before:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ReaderAdapterError("INVALID_PDF_ENRICHMENT") from None


class ArxivPdfResolver:
    def __init__(self, cache_dir, *, client=None, request_interval=3):
        self.cache_dir = Path(cache_dir).resolve()
        self.client = client
        self.request_interval = request_interval
        self.last_request = 0.0
        self.verified_bytes = {}

    async def _download(self, url, *, kind, limit):
        """同域有限重定向、流式大小限制、不携带 API Key。"""
        async def request(client):
            target = url
            for _ in range(4):
                if not arxiv_id(target, kind) or urlparse(target).scheme != "https":
                    raise PdfSourceError("UNSAFE_ARXIV_REDIRECT")
                delay = self.request_interval - (time.monotonic() - self.last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
                self.last_request = time.monotonic()
                async with client.stream("GET", target, follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = urljoin(target, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise PdfSourceError("ARXIV_HTTP_" + str(response.status_code))
                    length = response.headers.get("content-length")
                    if length and length.isdigit() and int(length) > limit:
                        raise PdfSourceError("SOURCE_TOO_LARGE")
                    chunks, count = [], 0
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > limit:
                            raise PdfSourceError("SOURCE_TOO_LARGE")
                        chunks.append(chunk)
                    return b"".join(chunks), target
            raise PdfSourceError("TOO_MANY_REDIRECTS")
        if self.client is not None:
            return await request(self.client)
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "ResearchMesh-PDF-Resolver/0.1"}) as client:
            return await request(client)

    def _cached(self, paper, sources, previous):
        for event in previous or []:
            if not isinstance(event, dict) or event.get("state") != "verified" or event.get("paperBefore") != paper:
                continue
            try:
                identity = arxiv_id(event["sourceUrl"], "abs")
                if not identity or identity not in {arxiv_id(u, "abs") for u in sources}:
                    continue
                if arxiv_id(event["pdfUrl"], "pdf") != identity:
                    continue
                digest = event["sha256"]
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    continue
                path = (self.cache_dir / (digest + ".pdf")).resolve()
                if path.parent != self.cache_dir or not path.is_file() or path.stat().st_size > MAX_PDF:
                    continue
                content = path.read_bytes()
                if sha256(content).hexdigest() != digest:
                    continue
                inspect_pdf(content, paper["title"])
                cached = deepcopy(event)
                cached.update(cacheReused=True, cachePath=str(path), networkAttempted=False)
                self.verified_bytes[event["pdfUrl"]] = content
                return cached
            except (ValueError, KeyError, TypeError, OSError):
                continue
        return None

    async def resolve(self, task, search_result, *, previous=None):
        resolved = deepcopy(task)
        events = []
        for paper in resolved["input"]["papers"]:
            if paper["pdfUrl"] is not None:
                continue  # 已提供的地址沿用原 Reader 行为，不替换或猜测地址。
            original = deepcopy(paper)
            sources = [paper["url"]] if paper["url"] else []
            for item in search_result["items"]:
                if item["kind"] == "paper" and paper_ref(item["data"]) == paper:
                    sources += item["data"].get("sourceUrls", [])
            sources = list(dict.fromkeys(s for s in sources if arxiv_id(s, "abs")))
            event = self._cached(original, sources, previous)
            if event is not None:
                paper["pdfUrl"] = event["pdfUrl"]
                events.append(event)
                continue
            failure = "NO_KNOWN_ARXIV_SOURCE" if not sources else "PDF_SOURCE_UNVERIFIED"
            for source in sources[:3]:
                identity = arxiv_id(source, "abs")
                canonical = "https://arxiv.org/abs/" + identity
                try:
                    html, final_page = await self._download(canonical, kind="abs", limit=512 * 1024)
                    if arxiv_id(final_page, "abs") != identity:
                        raise PdfSourceError("SOURCE_ID_MISMATCH")
                    page = ArxivPage()
                    page.feed(html.decode("utf-8", errors="replace"))
                    if not normalize_title(original["title"]) or normalize_title(page.title) != normalize_title(original["title"]):
                        raise PdfSourceError("SOURCE_TITLE_MISMATCH")
                    pdf_url = next((urljoin(final_page, link) for link in page.links
                                    if arxiv_id(urljoin(final_page, link), "pdf") == identity), None)
                    if pdf_url is None:
                        raise PdfSourceError("NO_VERIFIED_PDF_LINK")
                    content, final_pdf = await self._download(pdf_url, kind="pdf", limit=MAX_PDF)
                    if arxiv_id(final_pdf, "pdf") != identity:
                        raise PdfSourceError("PDF_ID_MISMATCH")
                    pages = inspect_pdf(content, original["title"])
                    digest = sha256(content).hexdigest()
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    path = self.cache_dir / (digest + ".pdf")
                    if not path.exists():
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix=".pdf.tmp", delete=False) as handle:
                                temporary = Path(handle.name)
                                handle.write(content)
                            temporary.replace(path)
                        finally:
                            if temporary is not None:
                                temporary.unlink(missing_ok=True)
                    elif sha256(path.read_bytes()).hexdigest() != digest:
                        raise PdfSourceError("PDF_CACHE_CONFLICT")
                    paper["pdfUrl"] = final_pdf
                    self.verified_bytes[final_pdf] = content
                    events.append({"paperId": paper["paperId"], "paperBefore": original, "state": "verified",
                        "sourceUrl": canonical, "pdfUrl": final_pdf, "pageTitle": page.title,
                        "sourcePageSha256": sha256(html).hexdigest(), "sha256": digest,
                        "bytes": len(content), "pages": pages, "cachePath": str(path), "cacheReused": False,
                        "verifiedAt": datetime.now().astimezone().isoformat(), "networkAttempted": True})
                    break
                except PdfSourceError as exc:
                    failure = str(exc)
                except httpx.HTTPError:
                    failure = "ARXIV_NETWORK_UNAVAILABLE"
                except OSError:
                    failure = "PDF_CACHE_UNAVAILABLE"
            else:
                events.append({"paperId": paper["paperId"], "paperBefore": original,
                               "state": "unavailable", "reason": failure, "networkAttempted": bool(sources)})
        validate_pdf_enrichment(task, resolved)
        return resolved, events

    async def fetch_pdf(self, url):
        """已核验全文复用本轮缓存；其他已有 PDF 仍交由原有下载逻辑。"""
        if url in self.verified_bytes:
            return self.verified_bytes[url]
        # 与原 PaperQAProvider 的已有 PDF 下载行为保持一致，无认证信息。
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                parts, count = [], 0
                async for part in response.aiter_bytes():
                    count += len(part)
                    if count > MAX_PDF:
                        # 由 Wrapper 原有 HTTPError 分支映射为全文不可用。
                        raise httpx.HTTPError("PDF_SIZE_LIMIT")
                    parts.append(part)
                return b"".join(parts)
