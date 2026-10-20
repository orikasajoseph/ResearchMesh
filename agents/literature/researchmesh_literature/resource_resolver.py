"""Bounded, evidence-based PDF and code repository discovery for Literature."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from html import unescape
import ipaddress
import json
import re
import time
from urllib.parse import quote, urlencode, urlparse
import unicodedata

import httpx

from .schemas import PaperRecord


_ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v\d+)?", re.I)
_GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_][A-Za-z0-9_.-]*)", re.I)
_META = re.compile(r'<meta\s+[^>]*name=["\']citation_title["\'][^>]*content=["\']([^"\']+)', re.I)


def _normalized(text: str | None) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text or "").casefold() if c.isalnum())


def _safe_https(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True
    except ValueError:
        return False


def _repo_root(url: str) -> str | None:
    match = _GITHUB.fullmatch(url.rstrip("/"))
    return f"https://github.com/{match[1]}/{match[2]}" if match else None


def _arxiv_id(raw: dict, paper: PaperRecord) -> str | None:
    external = raw.get("externalIds") or {}
    candidate = external.get("ArXiv")
    if isinstance(candidate, str) and _ARXIV_ID.fullmatch(candidate):
        return candidate
    for url in [paper.url, *paper.repositoryUrls]:
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if parsed.hostname in {"arxiv.org", "www.arxiv.org"}:
            match = re.fullmatch(r"/abs/(.+)", parsed.path)
            if match and _ARXIV_ID.fullmatch(match[1]):
                return match[1]
    return None


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes
    location: str | None = None


class ResourceResolver:
    def __init__(self, *, max_requests: int = 12, timeout_seconds: float = 5,
                 client: httpx.AsyncClient | None = None, trust_env: bool = True):
        self.max_requests = max_requests
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.request_count = 0
        self.rate_limited = False
        self.last_repository_reason = "NO_VERIFIED_REPOSITORY"
        self.trust_env = trust_env
        self.request_events: list[dict] = []

    async def __aenter__(self):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False,
                                            trust_env=self.trust_env,
                                            headers={"User-Agent": "ResearchMesh-Literature/0.1",
                                                     "Accept": "application/json,text/html,application/pdf,*/*"})
            self._owns_client = True
        else:
            self._owns_client = False
        return self

    async def __aexit__(self, *_):
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, url: str, *, limit: int = 262_144,
                   purpose: str = "resource") -> tuple[int, bytes, str]:
        for _ in range(3):
            if self.rate_limited:
                return 429, b"", "RATE_LIMITED"
            if not _safe_https(url):
                return 0, b"", "INVALID_URL"
            if self.request_count >= self.max_requests:
                return 0, b"", "REQUEST_BUDGET_EXHAUSTED"
            self.request_count += 1
            parsed = urlparse(url)
            phase = "connect"
            via_proxy = None if self.trust_env else False
            started = time.monotonic()
            event = {"purpose": purpose, "host": parsed.hostname, "pathId": self._path_id(parsed),
                     "status": None, "errorType": None, "phase": None,
                     "durationMs": None, "proxyMode": "inherit" if self.trust_env else "direct",
                     "viaProxy": None, "remainingBudget": self.max_requests - self.request_count}

            async def trace(name: str, info: dict) -> None:
                nonlocal phase, via_proxy
                if name == "connection.connect_tcp.started":
                    host = info.get("host")
                    if isinstance(host, bytes):
                        host = host.decode("ascii", "replace")
                    via_proxy = bool(host and host.casefold() != parsed.hostname.casefold())
                    phase = "tcp"
                elif name == "connection.start_tls.started":
                    phase = "tls"
                elif name.endswith("receive_response_headers.started"):
                    phase = "response_headers"
                elif name.endswith("receive_response_body.started"):
                    phase = "response_body"

            def finish(status: int | None, error_type: str | None = None) -> None:
                event.update(status=status, errorType=error_type, phase=phase,
                             durationMs=round((time.monotonic() - started) * 1000, 2),
                             viaProxy=via_proxy)
                self.request_events.append(event)
            try:
                async with self.client.stream("GET", url, headers={"Range": f"bytes=0-{limit - 1}"},
                                              extensions={"trace": trace}) as response:
                    if response.status_code == 429 or (response.status_code == 403 and (
                        response.headers.get("x-ratelimit-remaining") == "0" or response.headers.get("retry-after")
                    )):
                        self.rate_limited = True
                        finish(response.status_code, "RATE_LIMITED")
                        return response.status_code, b"", "RATE_LIMITED"
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            finish(response.status_code, "REDIRECT_MISSING")
                            return response.status_code, b"", "REDIRECT_MISSING"
                        finish(response.status_code)
                        from urllib.parse import urljoin
                        url = urljoin(url, location)
                        continue
                    if response.status_code not in (200, 206):
                        finish(response.status_code, f"HTTP_{response.status_code}")
                        return response.status_code, b"", f"HTTP_{response.status_code}"
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk[:max(0, limit - size)])
                        size += len(chunk)
                        if size >= limit:
                            break
                    finish(response.status_code)
                    return response.status_code, b"".join(chunks), "OK"
            except httpx.TimeoutException as exc:
                finish(None, type(exc).__name__)
                return 0, b"", "TIMEOUT"
            except httpx.HTTPError as exc:
                finish(None, type(exc).__name__)
                return 0, b"", "NETWORK_ERROR"
        return 0, b"", "TOO_MANY_REDIRECTS"

    @staticmethod
    def _path_id(parsed) -> str:
        path = parsed.path
        if parsed.hostname in {"arxiv.org", "www.arxiv.org"}:
            return "arxiv.pdf" if path.startswith("/pdf/") else "arxiv.abstract"
        if parsed.hostname == "api.github.com":
            return "github.search" if path == "/search/repositories" else "github.readme"
        if parsed.hostname == "api.openalex.org":
            return "openalex.work"
        return "other"

    async def _pdf(self, url: str) -> tuple[bool, str]:
        status, content, reason = await self._get(url, limit=32, purpose="verify_pdf")
        return status in (200, 206) and content.lstrip().startswith(b"%PDF-"), reason if reason != "OK" else "NOT_PDF"

    async def resolve(self, paper: PaperRecord, raw: dict) -> tuple[PaperRecord, dict]:
        paper = paper.model_copy(deep=True)
        arxiv = _arxiv_id(raw, paper)
        report = {"paperId": paper.paperId, "pdf": {"status": "missing", "reason": "NO_VERIFIED_PDF"},
                  "repository": {"status": "missing", "reason": "NO_VERIFIED_REPOSITORY"}}
        arxiv_page = f"https://arxiv.org/abs/{arxiv}" if arxiv else None
        page_text = None
        page_checked = False
        if paper.pdfUrl:
            ok, reason = await self._pdf(paper.pdfUrl)
            if ok:
                report["pdf"] = {"status": "verified", "source": ("openalex.best_oa_location" if raw.get("resourceProvider") == "openalex"
                                                                  else "semantic-scholar.openAccessPdf"),
                                 "evidenceUrl": paper.url, "url": paper.pdfUrl}
            else:
                report["pdf"]["reason"] = reason
                paper.pdfUrl = None
        if not paper.pdfUrl and arxiv:
            status, body, reason = await self._get(arxiv_page, purpose="find_pdf")
            page_checked = True
            page_text = body.decode("utf-8", "replace") if status in (200, 206) else None
            title = _META.search(page_text) if page_text else None
            if title and _normalized(unescape(title[1])) == _normalized(paper.title):
                pdf = f"https://arxiv.org/pdf/{arxiv}"
                ok, pdf_reason = await self._pdf(pdf)
                if ok:
                    paper.pdfUrl = pdf
                    report["pdf"] = {"status": "verified", "source": "arxiv.externalIds+title",
                                     "evidenceUrl": arxiv_page, "url": pdf}
                else:
                    report["pdf"]["reason"] = pdf_reason
            else:
                report["pdf"]["reason"] = "ARXIV_TITLE_MISMATCH" if title else reason
        if not paper.pdfUrl and paper.doi and raw.get("resourceProvider") != "openalex":
            url = "https://api.openalex.org/works/https://doi.org/" + quote(paper.doi, safe="/")
            status, body, reason = await self._get(url, purpose="openalex_pdf_metadata")
            if status in (200, 206):
                try:
                    work = json.loads(body)
                    same_doi = work.get("doi", "").casefold().removeprefix("https://doi.org/").rstrip("/") == paper.doi.casefold()
                    same_title = _normalized(work.get("display_name")) == _normalized(paper.title)
                    location = work.get("best_oa_location") or {}
                    candidate = location.get("pdf_url") if location.get("is_oa") and same_doi and same_title else None
                    if isinstance(candidate, str):
                        ok, pdf_reason = await self._pdf(candidate)
                        if ok:
                            paper.pdfUrl = candidate
                            report["pdf"] = {"status": "verified", "source": "openalex.best_oa_location",
                                             "evidenceUrl": url, "url": candidate}
                        else:
                            report["pdf"]["reason"] = pdf_reason
                    else:
                        report["pdf"]["reason"] = "OPENALEX_NO_MATCHING_PDF"
                except (ValueError, TypeError, AttributeError):
                    report["pdf"]["reason"] = "OPENALEX_INVALID_RESPONSE"
            else:
                report["pdf"]["reason"] = reason

        identifiers = [value for value in (arxiv, paper.doi) if value]
        direct = [root for url in paper.repositoryUrls if (root := _repo_root(url))]
        source_links = [url for url in paper.repositoryUrls if not _repo_root(url)]
        if arxiv and not direct and not page_checked and not self.rate_limited:
            status, body, _ = await self._get(arxiv_page, purpose="find_repository_on_paper_page")
            page_checked = True
            page_text = body.decode("utf-8", "replace") if status in (200, 206) else None
        page_candidates = []
        title = _META.search(page_text) if page_text else None
        if title and _normalized(unescape(title[1])) == _normalized(paper.title):
            page_candidates = list(dict.fromkeys(
                root for match in _GITHUB.finditer(page_text)
                if (root := _repo_root(match.group(0)))))[:2]
        abstract_candidates = list(dict.fromkeys(
            root for match in _GITHUB.finditer(raw.get("abstract") or "")
            if (root := _repo_root(match.group(0)))))[:2]
        found = await self._find_repository(paper, identifiers, direct, page_candidates,
                                            abstract_candidates, arxiv_page,
                                            source=raw.get("resourceProvider", "semantic-scholar"))
        if found:
            paper.repositoryUrls = list(dict.fromkeys([*source_links, found["url"]]))
            report["repository"] = found
        else:
            paper.repositoryUrls = list(dict.fromkeys(source_links))
            report["repository"]["reason"] = "RATE_LIMITED" if self.rate_limited else (
                "REQUEST_BUDGET_EXHAUSTED" if self.request_count >= self.max_requests else self.last_repository_reason)
        return paper, report

    async def _find_repository(self, paper: PaperRecord, identifiers: list[str], direct: list[str],
                               page_candidates: list[str] | None = None,
                               abstract_candidates: list[str] | None = None,
                               arxiv_page: str | None = None,
                               source: str = "semantic-scholar") -> dict | None:
        if not identifiers:
            self.last_repository_reason = "PAPER_IDENTIFIER_MISSING"
            return None
        candidates = [(url, source + ".paper-metadata", paper.url) for url in direct]
        candidates += [(url, source + ".abstract", paper.url) for url in abstract_candidates or []]
        candidates += [(url, "arxiv.paper-page", arxiv_page) for url in page_candidates or []]
        for candidate in candidates[:3]:
            found = await self._verify_repository(paper, identifiers, *candidate)
            if found:
                return found
        search_candidates = []
        if not self.rate_limited:
            title_prefix = (paper.title or "").split(":", 1)[0].strip()
            if not title_prefix:
                self.last_repository_reason = "PAPER_TITLE_MISSING"
                return None
            query = f"{title_prefix} in:name"
            search = "https://api.github.com/search/repositories?" + urlencode(
                {"q": query, "per_page": 5, "sort": "stars", "order": "desc"})
            status, body, reason = await self._get(search, limit=131_072,
                                                   purpose="search_repository")
            if status in (200, 206):
                try:
                    for item in json.loads(body).get("items", [])[:3]:
                        root = _repo_root(item.get("html_url", ""))
                        if root and not item.get("fork"):
                            search_candidates.append((root, "github.search+README",
                                                      "https://api.github.com/search/repositories"))
                except (ValueError, TypeError, AttributeError):
                    self.last_repository_reason = "GITHUB_SEARCH_INVALID_RESPONSE"
                    return None
            else:
                self.last_repository_reason = reason
        for candidate in search_candidates[:3]:
            found = await self._verify_repository(paper, identifiers, *candidate)
            if found:
                return found
        return None

    async def _verify_repository(self, paper: PaperRecord, identifiers: list[str],
                                  url: str, source: str, discovery_url: str | None) -> dict | None:
        parts = _GITHUB.fullmatch(url)
        api = f"https://api.github.com/repos/{parts[1]}/{parts[2]}/readme"
        status, body, reason = await self._get(api, limit=131_072,
                                               purpose="verify_repository_readme")
        if status not in (200, 206):
            self.last_repository_reason = reason
            return None
        try:
            document = json.loads(body)
            if document.get("encoding") != "base64":
                self.last_repository_reason = "GITHUB_README_INVALID_RESPONSE"
                return None
            readme = base64.b64decode(document["content"], validate=False).decode("utf-8", "replace")
        except (ValueError, KeyError, TypeError):
            self.last_repository_reason = "GITHUB_README_INVALID_RESPONSE"
            return None
        first = readme[:12_000]
        if any(re.search(r"\b" + re.escape(identifier) + r"\b", first, re.I) for identifier in identifiers) and (
            _normalized(paper.title) in _normalized(first[:4000])
            and not re.search(r"\b(?:unofficial|reimplementation|unrelated|third.party implementation)\b", first[:4000], re.I)
            and re.search(r"\b(?:paper|research|model|code|implementation)\b", first[:4000], re.I)
        ):
            evidence_url = document.get("html_url")
            if not isinstance(evidence_url, str) or not evidence_url.startswith(url + "/blob/"):
                evidence_url = url + "/blob/HEAD/README.md"
            return {"status": "verified", "source": source, "discoveryUrl": discovery_url,
                    "evidenceUrl": evidence_url,
                    "identity": identifiers[0], "url": url}
        self.last_repository_reason = "GITHUB_README_IDENTITY_MISMATCH"
        return None
