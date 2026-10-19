import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _get_value(name: str, legacy_name: str, default: str) -> str:
    return os.getenv(name, os.getenv(legacy_name, default))


def _get_float(name: str, legacy_name: str, default: float) -> float:
    return float(_get_value(name, legacy_name, str(default)))


def _get_int(name: str, legacy_name: str, default: int) -> int:
    return int(_get_value(name, legacy_name, str(default)))


@dataclass(frozen=True)
class LiteratureAgentConfig:
    api_key: str | None = None
    timeout_seconds: float = 30.0
    rate_limit_per_second: float = 1.0 / 1.1
    max_retries: int = 3
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 8.0
    cache_ttl_seconds: float = 3600.0
    cache_max_entries: int = 256
    resolve_resources: bool = False
    resource_max_papers: int = 3
    resource_max_requests: int = 12
    resource_timeout_seconds: float = 5.0
    resource_trust_env: bool = True
    search_provider: str = "semantic-scholar"
    openalex_fallback: bool = False
    openalex_api_key: str | None = None
    openalex_timeout_seconds: float = 8.0
    openalex_max_results: int = 5
    openalex_trust_env: bool = True

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.rate_limit_per_second <= 0:
            raise ValueError("rate_limit_per_second must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.backoff_initial_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("backoff values cannot be negative")
        if self.cache_ttl_seconds < 0 or self.cache_max_entries < 1:
            raise ValueError("cache settings are invalid")
        if self.resource_max_papers < 1 or self.resource_max_requests < 1 or self.resource_timeout_seconds <= 0:
            raise ValueError("resource limits are invalid")
        if not isinstance(self.resource_trust_env, bool):
            raise ValueError("Resource Resolver proxy inheritance setting is invalid")
        if self.search_provider not in {"semantic-scholar", "openalex"}:
            raise ValueError("search_provider is invalid")
        if self.openalex_timeout_seconds <= 0 or not 1 <= self.openalex_max_results <= 25:
            raise ValueError("OpenAlex limits are invalid")
        if not isinstance(self.openalex_trust_env, bool):
            raise ValueError("OpenAlex proxy inheritance setting is invalid")

    @classmethod
    def from_env(cls) -> "LiteratureAgentConfig":
        load_dotenv(_ENV_FILE)
        if "LITERATURE_RATE_LIMIT_SECONDS" in os.environ:
            rate_limit_seconds = float(os.environ["LITERATURE_RATE_LIMIT_SECONDS"])
            if rate_limit_seconds <= 0:
                raise ValueError(
                    "LITERATURE_RATE_LIMIT_SECONDS must be greater than zero"
                )
            rate_limit_per_second = 1.0 / rate_limit_seconds
        else:
            rate_limit_per_second = float(
                os.getenv("RESEARCHMESH_SS_RATE_LIMIT_PER_SECOND", str(1.0 / 1.1))
            )
        return cls(
            api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"),
            timeout_seconds=_get_float(
                "LITERATURE_REQUEST_TIMEOUT",
                "RESEARCHMESH_SS_TIMEOUT_SECONDS",
                30.0,
            ),
            rate_limit_per_second=rate_limit_per_second,
            max_retries=_get_int(
                "LITERATURE_MAX_RETRIES", "RESEARCHMESH_SS_MAX_RETRIES", 3
            ),
            backoff_initial_seconds=_get_float(
                "LITERATURE_BACKOFF_INITIAL_SECONDS",
                "RESEARCHMESH_SS_BACKOFF_INITIAL_SECONDS",
                1.0,
            ),
            backoff_max_seconds=_get_float(
                "LITERATURE_BACKOFF_MAX_SECONDS",
                "RESEARCHMESH_SS_BACKOFF_MAX_SECONDS",
                8.0,
            ),
            cache_ttl_seconds=_get_float(
                "LITERATURE_CACHE_TTL_SECONDS",
                "RESEARCHMESH_SS_CACHE_TTL_SECONDS",
                3600.0,
            ),
            cache_max_entries=_get_int(
                "LITERATURE_CACHE_MAX_ENTRIES",
                "RESEARCHMESH_SS_CACHE_MAX_ENTRIES",
                256,
            ),
            search_provider=os.getenv("LITERATURE_SEARCH_PROVIDER", "semantic-scholar"),
            openalex_fallback=os.getenv("LITERATURE_OPENALEX_FALLBACK", "1").lower() in {"1", "true", "yes"},
            openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
            openalex_timeout_seconds=float(os.getenv("LITERATURE_OPENALEX_TIMEOUT", "8")),
            openalex_max_results=int(os.getenv("LITERATURE_OPENALEX_MAX_RESULTS", "5")),
            openalex_trust_env=_parse_openalex_trust_env(),
            resource_trust_env=_parse_trust_env("LITERATURE_RESOURCE_TRUST_ENV"),
        )


def _parse_openalex_trust_env() -> bool:
    return _parse_trust_env("LITERATURE_OPENALEX_TRUST_ENV")


def _parse_trust_env(name: str) -> bool:
    value = os.getenv(name, "1").strip().lower()
    if value not in {"1", "true", "yes", "0", "false", "no"}:
        raise ValueError(f"{name} must be a boolean")
    return value in {"1", "true", "yes"}
