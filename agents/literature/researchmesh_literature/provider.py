import asyncio
import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from tenacity import RetryError

from semanticscholar import AsyncSemanticScholar
from semanticscholar.SemanticScholarException import (
    BadQueryParametersException,
    GatewayTimeoutException,
    InternalServerErrorException,
    SemanticScholarException,
    ServerErrorException,
)

from .config import LiteratureAgentConfig
from .schemas import LiteratureSearchRequest, PaperAuthor, PaperRecord
from semantic_http import create_monitored_client, client_diagnostics


class ProviderError(Exception):
    def __init__(self, message: str, attempts: int = 0) -> None:
        super().__init__(message)
        self.attempts = attempts


class ProviderRequestError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


@dataclass
class ProviderSearchResult:
    papers: list[PaperRecord]
    total: int
    attempts: int
    cache_hit: bool


class _AsyncRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self._minimum_interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._minimum_interval - (now - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


class SemanticScholarProvider:
    SEARCH_FIELDS = [
        "paperId", "title", "authors", "year", "abstract", "externalIds",
        "url", "openAccessPdf", "citationCount",
    ]

    def __init__(
        self,
        config: LiteratureAgentConfig | None = None,
        client: AsyncSemanticScholar | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_observer: Callable[[dict[str, object]], None] | None = None,
        request_source: str = "literature-agent",
        subtask_id: str | None = None,
    ) -> None:
        self.config = config or LiteratureAgentConfig.from_env()
        self.client = client if client is not None else create_monitored_client(
            self.config, source=request_source, subtask_id=subtask_id)
        self._sleep = sleep
        self._retry_observer = retry_observer
        self._limiter = _AsyncRateLimiter(self.config.rate_limit_per_second)
        self._cache: dict[str, tuple[float, list[PaperRecord], int]] = {}
        self._cache_lock = asyncio.Lock()

    def http_diagnostics(self):
        """仅返回安全诊断，绝不加入 paper_records 业务输出。"""
        return client_diagnostics(self.client, self.config.api_key)

    async def search(self, request: LiteratureSearchRequest) -> ProviderSearchResult:
        cache_key = json.dumps(request.model_dump(), sort_keys=True)
        cached = await self._get_cached(cache_key)
        if cached is not None:
            papers, total = cached
            return ProviderSearchResult(papers, total, 0, True)

        for attempt in range(1, self.config.max_retries + 2):
            try:
                await self._limiter.acquire()
                filters = request.filters
                results = await self.client.search_paper(
                    query=request.query,
                    year=filters.year,
                    publication_types=filters.publicationTypes or None,
                    open_access_pdf=filters.openAccessOnly or None,
                    venue=filters.venues or None,
                    fields_of_study=filters.fieldsOfStudy or None,
                    fields=self.SEARCH_FIELDS,
                    publication_date_or_year=filters.publicationDateOrYear,
                    min_citation_count=filters.minCitationCount,
                    limit=request.limit,
                )
                papers = [self._to_record(item.raw_data) for item in results.items]
                total = getattr(results, "total", len(papers))
                await self._set_cached(cache_key, papers, total)
                return ProviderSearchResult(papers, total, attempt, False)
            except (ConnectionRefusedError, RetryError) as exc:
                original = self._unwrap_retry_error(exc)
                if not isinstance(original, ConnectionRefusedError):
                    raise ProviderUnavailableError(
                        str(original), attempts=attempt
                    ) from exc
                if attempt > self.config.max_retries:
                    raise ProviderRateLimitError(
                        str(original), attempts=attempt
                    ) from exc
                await self._backoff(attempt, "rate_limit", 429)
            except BadQueryParametersException as exc:
                raise ProviderRequestError(str(exc), attempts=attempt) from exc
            except ServerErrorException as exc:
                if attempt > self.config.max_retries:
                    raise ProviderUnavailableError(
                        str(exc), attempts=attempt
                    ) from exc
                status = 500 if isinstance(exc, InternalServerErrorException) else 504
                await self._backoff(attempt, "server_error", status)
            except (httpx.TimeoutException, TimeoutError) as exc:
                if attempt > self.config.max_retries:
                    raise ProviderUnavailableError(
                        str(exc), attempts=attempt
                    ) from exc
                await self._backoff(attempt, "timeout", None)
            except httpx.HTTPError as exc:
                if attempt > self.config.max_retries:
                    raise ProviderUnavailableError(
                        str(exc), attempts=attempt
                    ) from exc
                await self._backoff(attempt, "network_error", None)
            except SemanticScholarException as exc:
                raise ProviderUnavailableError(
                    str(exc), attempts=attempt
                ) from exc
            except Exception as exc:
                raise ProviderUnavailableError(
                    str(exc), attempts=attempt
                ) from exc

        raise ProviderUnavailableError("Semantic Scholar request failed")

    async def _backoff(
        self,
        attempt: int,
        reason: str,
        http_status: int | None,
    ) -> None:
        delay = min(
            self.config.backoff_initial_seconds * (2 ** (attempt - 1)),
            self.config.backoff_max_seconds,
        )
        if self._retry_observer is not None:
            self._retry_observer({
                "retryNumber": attempt,
                "failedAttempt": attempt,
                "reason": reason,
                "httpStatus": http_status,
                "backoffSeconds": delay,
            })
        await self._sleep(delay)

    @staticmethod
    def _unwrap_retry_error(exc: Exception) -> Exception:
        if isinstance(exc, RetryError):
            original = exc.last_attempt.exception()
            return original if original is not None else exc
        return exc

    async def _get_cached(
        self, key: str
    ) -> tuple[list[PaperRecord], int] | None:
        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            expires_at, papers, total = cached
            if expires_at <= time.monotonic():
                del self._cache[key]
                return None
            return [paper.model_copy(deep=True) for paper in papers], total

    async def _set_cached(
        self, key: str, papers: list[PaperRecord], total: int
    ) -> None:
        if self.config.cache_ttl_seconds == 0:
            return
        async with self._cache_lock:
            while len(self._cache) >= self.config.cache_max_entries:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (
                time.monotonic() + self.config.cache_ttl_seconds,
                [paper.model_copy(deep=True) for paper in papers],
                total,
            )

    @staticmethod
    def _to_record(data: dict) -> PaperRecord:
        external_ids = data.get("externalIds") or {}
        pdf_url = (data.get("openAccessPdf") or {}).get("url") or None
        repository_urls = [url for url in [pdf_url] if url]
        repository_templates = {
            "ArXiv": "https://arxiv.org/abs/{}",
            "PubMed": "https://pubmed.ncbi.nlm.nih.gov/{}/",
            "PubMedCentral": "https://www.ncbi.nlm.nih.gov/pmc/articles/{}/",
            "ACL": "https://aclanthology.org/{}/",
        }
        for key, template in repository_templates.items():
            if external_ids.get(key):
                repository_urls.append(template.format(external_ids[key]))

        authors = [
            PaperAuthor(authorId=author.get("authorId"), name=author.get("name") or "")
            for author in (data.get("authors") or [])
        ]
        return PaperRecord(
            paperId=data.get("paperId") or "",
            title=data.get("title"),
            authors=authors,
            year=data.get("year"),
            abstract=data.get("abstract"),
            doi=external_ids.get("DOI"),
            url=data.get("url"),
            pdfUrl=pdf_url,
            repositoryUrls=list(dict.fromkeys(repository_urls)),
            citationCount=data.get("citationCount"),
        )
