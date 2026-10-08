import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .exceptions import LLMConfigurationError


_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int

    def __post_init__(self) -> None:
        if not self.api_key:
            raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")
        if not self.base_url:
            raise LLMConfigurationError("DEEPSEEK_BASE_URL is not configured")
        if not self.model:
            raise LLMConfigurationError("DEEPSEEK_MODEL is not configured")
        if self.timeout_seconds <= 0:
            raise LLMConfigurationError("LLM_REQUEST_TIMEOUT must be positive")
        if self.max_retries < 0:
            raise LLMConfigurationError("LLM_MAX_RETRIES cannot be negative")

    @classmethod
    def from_env(cls) -> "LLMConfig":
        load_dotenv(_ENV_FILE)
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            timeout_seconds=float(os.getenv("LLM_REQUEST_TIMEOUT", "60")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        )
