from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


@dataclass(frozen=True)
class PaperReaderConfig:
    api_key: str
    api_base: str
    model: str
    embedding: str = "st-multi-qa-MiniLM-L6-cos-v1"
    evidence_k: int = 6
    max_concurrent_requests: int = 4
    download_timeout_seconds: float = 60.0
    max_pdf_bytes: int = 50 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY is required")
        if not self.api_base.strip():
            raise ValueError("DEEPSEEK_BASE_URL is required")
        if not self.model.strip():
            raise ValueError("DEEPSEEK_MODEL is required")
        if self.evidence_k < 1:
            raise ValueError("evidence_k must be positive")
        if self.max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be positive")
        if self.download_timeout_seconds <= 0 or self.max_pdf_bytes < 1:
            raise ValueError("PDF download limits must be positive")

    @property
    def provider_model(self) -> str:
        model = self.model.strip()
        if model.startswith("deepseek/"):
            return model
        if "/" in model:
            raise ValueError(
                "DEEPSEEK_MODEL must be plain or use the 'deepseek/' provider prefix"
            )
        return f"deepseek/{model}"

    @classmethod
    def from_env(cls) -> "PaperReaderConfig":
        load_dotenv(ENV_FILE)
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            api_base=os.getenv("DEEPSEEK_BASE_URL", ""),
            model=os.getenv("DEEPSEEK_MODEL", ""),
        )
