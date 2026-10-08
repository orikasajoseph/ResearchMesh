import time

from .provider import (
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderUnavailableError,
    SemanticScholarProvider,
)
from .schemas import (
    AgentError,
    LiteratureSearchRequest,
    PaperRecordsResponse,
    RequestMetrics,
)


class LiteratureAgent:
    def __init__(self, provider: SemanticScholarProvider | None = None) -> None:
        self.provider = provider or SemanticScholarProvider()

    async def literature_search(
        self, request: LiteratureSearchRequest
    ) -> PaperRecordsResponse:
        started_at = time.perf_counter()
        try:
            result = await self.provider.search(request)
            duration_ms = (time.perf_counter() - started_at) * 1000
            metrics = RequestMetrics(
                durationMs=round(duration_ms, 3),
                attempts=result.attempts,
                cacheHit=result.cache_hit,
            )
            if not result.papers:
                return self._failure(
                    request.requestId,
                    metrics,
                    "NO_RESULTS",
                    "No papers matched the search request.",
                    False,
                )
            return PaperRecordsResponse(
                requestId=request.requestId,
                status="success",
                papers=result.papers,
                total=result.total,
                metrics=metrics,
            )
        except ProviderRateLimitError as exc:
            return self._exception_failure(request, started_at, "RATE_LIMITED", exc, True)
        except ProviderRequestError as exc:
            return self._exception_failure(request, started_at, "INVALID_REQUEST", exc, False)
        except ProviderUnavailableError as exc:
            return self._exception_failure(
                request, started_at, "PROVIDER_UNAVAILABLE", exc, True
            )

    @staticmethod
    def _exception_failure(
        request: LiteratureSearchRequest,
        started_at: float,
        code: str,
        exc: Exception,
        retryable: bool,
    ) -> PaperRecordsResponse:
        metrics = RequestMetrics(
            durationMs=round((time.perf_counter() - started_at) * 1000, 3),
            attempts=getattr(exc, "attempts", 0),
            cacheHit=False,
        )
        return LiteratureAgent._failure(
            request.requestId, metrics, code, str(exc), retryable
        )

    @staticmethod
    def _failure(
        request_id: str,
        metrics: RequestMetrics,
        code: str,
        message: str,
        retryable: bool,
    ) -> PaperRecordsResponse:
        return PaperRecordsResponse(
            requestId=request_id,
            status="failure",
            metrics=metrics,
            error=AgentError(code=code, message=message, retryable=retryable),
        )
