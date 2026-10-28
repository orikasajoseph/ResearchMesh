from __future__ import annotations

from dataclasses import dataclass


CODEWIKI_VERSION = "2.0.0"
CODEWIKI_COMMIT = "f0975ea19b17568ceb55cd5422206e1a1ae13c0b"
PROVIDER_NAME = "codewiki-static"


@dataclass(frozen=True)
class RepositoryAgentConfig:
    clone_timeout_seconds: float = 90.0
    checkout_timeout_seconds: float = 60.0
    git_verify_timeout_seconds: float = 15.0
    max_text_file_bytes: int = 64 * 1024
    max_enrichment_evidence_chars: int = 32_000
    debug_keep_temp: bool = False

    def __post_init__(self) -> None:
        if self.clone_timeout_seconds <= 0:
            raise ValueError("clone_timeout_seconds must be positive")
        if self.checkout_timeout_seconds <= 0:
            raise ValueError("checkout_timeout_seconds must be positive")
        if self.git_verify_timeout_seconds <= 0:
            raise ValueError("git_verify_timeout_seconds must be positive")
        if self.max_text_file_bytes < 1024:
            raise ValueError("max_text_file_bytes must be at least 1024")
        if self.max_enrichment_evidence_chars < 1024:
            raise ValueError("max_enrichment_evidence_chars must be at least 1024")
