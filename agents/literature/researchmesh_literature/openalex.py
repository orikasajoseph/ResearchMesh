"""One-request OpenAlex search used by the existing Literature provider."""
from __future__ import annotations

import re
import socket
import ssl
import unicodedata

import httpx

from .schemas import PaperAuthor, PaperRecord


_ARXIV = re.compile(r"(?:arxiv[:./ ]+)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_QUERY_ARXIV = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s]+", re.I)
_WORK = re.compile(r"https://openalex\.org/W\d+")


def _transport_category(exc: Exception) -> str:
    """Classify nested network failures without persisting exception messages or URLs."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return "TLS_CERTIFICATE"
        if isinstance(current, ssl.SSLEOFError):
            return "TLS_EOF"
        if isinstance(current, socket.gaierror):
            return "DNS"
        if isinstance(current, ConnectionRefusedError):
            return "CONNECTION_REFUSED"
        if isinstance(current, ConnectionResetError):
            return "CONNECTION_RESET"
        current = current.__cause__ or current.__context__
    if isinstance(exc, httpx.ConnectTimeout):
        return "CONNECT_TIMEOUT"
    if isinstance(exc, httpx.ReadTimeout):
        return "READ_TIMEOUT"
    if isinstance(exc, httpx.TimeoutException):
        return "TIMEOUT"
    return "TRANSPORT"


def _norm(value: str | None) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value or "").casefold() if char.isalnum())


def _doi(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.removeprefix("https://doi.org/").removeprefix("http://dx.doi.org/").rstrip("/")
    return value if _DOI.fullmatch(value) else None


def _abstract(index: dict | None) -> str | None:
    if not isinstance(index, dict):
        return None
    slots = [(position, word) for word, positions in index.items() if isinstance(positions, list)
             for position in positions if isinstance(position, int) and 0 <= position < 5000]
    if not slots:
        return None
    slots.sort()
    return " ".join(word for _, word in slots)[:12000]


def _arxiv(work: dict) -> str | None:
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    candidates = [work.get("doi"), ids.get("doi")]
    for location in work.get("locations") or []:
        if isinstance(location, dict):
            candidates.extend(location.get(key) for key in ("landing_page_url", "pdf_url"))
    for candidate in candidates:
        if isinstance(candidate, str) and (match := _ARXIV.search(candidate)):
            return match[1]
    return None


def map_work(work: dict, query: str) -> tuple[PaperRecord, dict] | None:
    """Reject mismatched identifiers and title-only fuzzy hits."""
    if not isinstance(work, dict) or not isinstance(work.get("id"), str) or not _WORK.fullmatch(work["id"]):
        return None
    title = work.get("display_name")
    doi = _doi(work.get("doi"))
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    ids_doi = _doi(ids.get("doi"))
    if not isinstance(title, str) or not title.strip() or (doi and ids_doi and doi.casefold() != ids_doi.casefold()):
        return None
    doi = doi or ids_doi
    arxiv = _arxiv(work)
    expected_arxiv = _QUERY_ARXIV.search(query)
    expected_doi = _DOI.search(query)
    if expected_arxiv:
        matched = arxiv == expected_arxiv[1]
    elif expected_doi:
        matched = bool(doi and doi.casefold() == expected_doi[0].rstrip(".,;").casefold())
    else:
        words = [part for part in re.findall(r"[\w]+", query.casefold()) if len(part) > 2]
        title_words = set(re.findall(r"[\w]+", title.casefold()))
        matched = bool(doi and (_norm(title) == _norm(query)
                                 or (len(words) <= 3 and words and all(word in title_words for word in words))))
    if not matched:
        return None
    location = work.get("best_oa_location") or {}
    pdf = location.get("pdf_url") if isinstance(location, dict) and location.get("is_oa") is True else None
    if not isinstance(pdf, str) or not pdf.startswith("https://"):
        pdf = None
    authors = []
    for entry in (work.get("authorships") or [])[:30]:
        author = entry.get("author") if isinstance(entry, dict) else None
        if isinstance(author, dict) and isinstance(author.get("display_name"), str):
            authors.append(PaperAuthor(authorId=author.get("id"), name=author["display_name"]))
    source_links = [f"https://arxiv.org/abs/{arxiv}"] if arxiv else []
    record = PaperRecord(paperId=work["id"], title=title, authors=authors,
                         year=work.get("publication_year"), abstract=_abstract(work.get("abstract_inverted_index")),
                         doi=doi, url=work["id"], pdfUrl=pdf, repositoryUrls=source_links,
                         citationCount=work.get("cited_by_count"))
    raw = {"externalIds": {"DOI": doi, "ArXiv": arxiv}, "abstract": record.abstract,
           "resourceProvider": "openalex"}
    return record, raw


async def search_openalex(query: str, limit: int, *, timeout: float, api_key: str | None = None,
                          client: httpx.AsyncClient | None = None,
                          trust_env: bool = True) -> tuple[list[PaperRecord], list[dict], int, dict]:
    """Exactly one Works HTTP request; no retries or pagination."""
    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=trust_env)
    headers = {"Accept": "application/json", "User-Agent": "ResearchMesh-Literature/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    diagnostics = {"providerUsed": "openalex", "openalexRequests": 1}
    if owned:
        diagnostics["openalexTrustEnv"] = trust_env
    try:
        response = await client.get("https://api.openalex.org/works", params={
            "search": query, "per_page": min(limit, 25),
            "select": "id,ids,doi,display_name,authorships,publication_year,abstract_inverted_index,best_oa_location,locations,cited_by_count",
        }, headers=headers, timeout=timeout)
        diagnostics["openalexHttpStatus"] = response.status_code
        diagnostics["openalexRetryAfter"] = response.headers.get("retry-after")
        diagnostics["openalexRateLimitHeaders"] = {
            name: response.headers[name] for name in (
                "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-credits-used", "x-ratelimit-reset")
            if name in response.headers}
        if response.status_code != 200:
            raise OpenAlexError(f"OPENALEX_HTTP_{response.status_code}", diagnostics)
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise OpenAlexError("OPENALEX_INVALID_RESPONSE", diagnostics)
        mapped = [entry for work in data["results"] if (entry := map_work(work, query)) is not None]
        if not mapped:
            raise OpenAlexError("OPENALEX_NO_VERIFIED_MATCH", diagnostics)
        diagnostics["verifiedResults"] = len(mapped)
        return [entry[0] for entry in mapped], [entry[1] for entry in mapped], len(mapped), diagnostics
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        diagnostics["openalexTransportError"] = type(exc).__name__
        diagnostics["openalexTransportCategory"] = _transport_category(exc)
        raise OpenAlexError("OPENALEX_TRANSPORT_ERROR", diagnostics) from exc
    except ValueError as exc:
        raise OpenAlexError("OPENALEX_INVALID_RESPONSE", diagnostics) from exc
    finally:
        if owned:
            await client.aclose()


class OpenAlexError(Exception):
    def __init__(self, code: str, diagnostics: dict):
        super().__init__(code)
        self.diagnostics = diagnostics
