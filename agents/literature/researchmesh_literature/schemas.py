from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchMeshModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class LiteratureSearchFilters(ResearchMeshModel):
    year: str | None = None
    publicationDateOrYear: str | None = None
    publicationTypes: list[str] = Field(default_factory=list)
    venues: list[str] = Field(default_factory=list)
    fieldsOfStudy: list[str] = Field(default_factory=list)
    openAccessOnly: bool = False
    minCitationCount: int | None = Field(default=None, ge=0)


class LiteratureSearchRequest(ResearchMeshModel):
    schemaVersion: Literal["literature_search.v0.1"] = "literature_search.v0.1"
    requestId: str = Field(min_length=1)
    query: str = Field(min_length=1)
    filters: LiteratureSearchFilters = Field(
        default_factory=LiteratureSearchFilters
    )
    limit: int = Field(default=20, ge=1, le=100)


class PaperAuthor(ResearchMeshModel):
    authorId: str | None = None
    name: str


class PaperRecord(ResearchMeshModel):
    paperId: str
    title: str | None = None
    authors: list[PaperAuthor] = Field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    pdfUrl: str | None = None
    repositoryUrls: list[str] = Field(default_factory=list)
    citationCount: int | None = None


class RequestMetrics(ResearchMeshModel):
    durationMs: float = Field(ge=0)
    attempts: int = Field(ge=0)
    cacheHit: bool = False


class AgentError(ResearchMeshModel):
    code: str
    message: str
    retryable: bool = False


class PaperRecordsResponse(ResearchMeshModel):
    schemaVersion: Literal["paper_records.v1"] = "paper_records.v1"
    requestId: str
    status: Literal["success", "failure"]
    papers: list[PaperRecord] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    provider: Literal["semantic-scholar"] = "semantic-scholar"
    metrics: RequestMetrics
    error: AgentError | None = None
