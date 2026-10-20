from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def _validate_http_url(value: str, field_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    return value


class PaperReaderModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class PaperRef(PaperReaderModel):
    paperId: str = Field(min_length=1, pattern=r"\S")
    title: str | None = None
    doi: str | None = None
    url: str | None = None
    pdfUrl: str | None = None
    repositoryUrls: list[str] = Field(default_factory=list)

    @field_validator("title", "doi", "url", "pdfUrl", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, value: str | None) -> str | None:
        if value is not None and not DOI_PATTERN.fullmatch(value):
            raise ValueError("doi must be a valid DOI beginning with '10.'")
        return value

    @field_validator("url", "pdfUrl")
    @classmethod
    def validate_urls(cls, value: str | None, info) -> str | None:
        if value is not None:
            return _validate_http_url(value, info.field_name)
        return value

    @field_validator("repositoryUrls")
    @classmethod
    def validate_repository_urls(cls, values: list[str]) -> list[str]:
        return [_validate_http_url(value, "repositoryUrls") for value in values]

    @model_validator(mode="after")
    def require_resolvable_identifier(self) -> "PaperRef":
        if not any((self.doi, self.url, self.pdfUrl)):
            raise ValueError("at least one of doi, url, or pdfUrl must be provided")
        return self


class PaperReadInput(PaperReaderModel):
    papers: list[PaperRef] = Field(min_length=1)


class PaperReadConstraints(PaperReaderModel):
    maxPapers: Literal[1] = 1


class PaperReadTask(PaperReaderModel):
    schemaVersion: Literal["0.2"]
    subtaskId: str = Field(min_length=1, pattern=r"\S")
    taskType: Literal["paper_read"]
    goal: str = Field(min_length=1, pattern=r"\S")
    input: PaperReadInput
    constraints: PaperReadConstraints = Field(default_factory=PaperReadConstraints)
    outputSchema: Literal["paper_notes.v1"]

    @model_validator(mode="after")
    def enforce_max_papers(self) -> "PaperReadTask":
        if len(self.input.papers) > self.constraints.maxPapers:
            raise ValueError(
                f"input.papers exceeds constraints.maxPapers={self.constraints.maxPapers}"
            )
        return self


AnalysisLevel = Literal["abstract", "fulltext"]


class PaperEvidence(PaperReaderModel):
    sourceId: str = Field(min_length=1, pattern=r"\S")
    url: str | None = None
    locator: str = Field(min_length=1, pattern=r"\S")
    supports: str = Field(min_length=1, pattern=r"\S")
    text: str | None = None
    summary: str | None = None
    score: int | float | None = None

    @field_validator("url")
    @classmethod
    def validate_evidence_url(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_http_url(value, "url")
        return value


class PaperNoteData(PaperReaderModel):
    source: PaperRef
    method: str = Field(min_length=1, pattern=r"\S")
    findings: str = Field(min_length=1, pattern=r"\S")
    limitations: str = Field(min_length=1, pattern=r"\S")
    analysisLevel: AnalysisLevel
    evidence: list[PaperEvidence] = Field(min_length=1)


class PaperNoteItem(PaperReaderModel):
    kind: Literal["paper_note"] = "paper_note"
    data: PaperNoteData


class PaperReadResult(PaperReaderModel):
    schemaVersion: Literal["0.2"] = "0.2"
    subtaskId: str = Field(min_length=1, pattern=r"\S")
    taskType: Literal["paper_read"] = "paper_read"
    summary: str = Field(min_length=1, pattern=r"\S")
    items: list[PaperNoteItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
