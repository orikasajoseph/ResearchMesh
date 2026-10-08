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
        )
