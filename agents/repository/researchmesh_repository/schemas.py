from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RepositoryTaskInput(StrictModel):
    repositoryUrls: list[str] = Field(default_factory=list)


class RepositoryTaskConstraints(StrictModel):
    maxRepositories: Literal[1] = 1
    executeCode: Literal[False] = False


class RepositoryAnalyzeRequest(StrictModel):
    """Provisional Partner business request for the target Task v0.2 contract."""

    schemaVersion: Literal["0.2"]
    subtaskId: str = Field(min_length=1)
    taskType: Literal["repository_analyze"]
    goal: str = Field(min_length=1)
    input: RepositoryTaskInput
    constraints: RepositoryTaskConstraints
    outputSchema: Literal["code_records.v1"]


class Evidence(StrictModel):
    evidenceId: str
    sourceType: str
    filePath: str
    symbol: str | None = None
    startLine: int | None = Field(default=None, ge=1)
    endLine: int | None = Field(default=None, ge=1)
    text: str
    supports: list[str] = Field(default_factory=list)


class StructureComponent(StrictModel):
    componentId: str
    name: str
    componentType: str
    symbol: str | None = None
    startLine: int | None = None
    endLine: int | None = None
    evidenceId: str


class StructureFile(StrictModel):
    filePath: str
    components: list[StructureComponent] = Field(default_factory=list)


class Dependency(StrictModel):
    source: str
    target: str
    evidenceIds: list[str]


class Entrypoint(StrictModel):
    name: str
    entrypointType: str
    target: str
    source: str
    evidenceIds: list[str]


class RunCommand(StrictModel):
    command: str
    source: str
    type: Literal["declared"] = "declared"
    evidenceIds: list[str]


class ReproductionBarrier(StrictModel):
    issue: str
    evidenceIds: list[str]


class Overview(StrictModel):
    text: str
    evidenceIds: list[str]


class RepositoryIdentity(StrictModel):
    url: str
    commitHash: str


class AnalysisMetrics(StrictModel):
    cloneMs: float = Field(ge=0)
    staticAnalysisMs: float = Field(ge=0)
    llmEnrichmentMs: float = Field(ge=0)
    totalMs: float = Field(ge=0)


class AnalysisCounts(StrictModel):
    componentCount: int = Field(ge=0)
    dependencyEdgeCount: int = Field(ge=0)
    artifactCount: int = Field(ge=0)
    evidenceCount: int = Field(ge=0)


class Diagnostics(StrictModel):
    provider: Literal["codewiki-static"] = "codewiki-static"
    codewikiVersion: str
    codewikiCommit: str
    metrics: AnalysisMetrics
    counts: AnalysisCounts


class RepositoryRecord(StrictModel):
    repository: RepositoryIdentity
    structure: list[StructureFile]
    dependencies: list[Dependency]
    entrypoints: list[Entrypoint]
    runCommands: list[RunCommand]
    reproductionBarriers: list[ReproductionBarrier]
    overview: Overview | None = None
    evidence: list[Evidence]
    diagnostics: Diagnostics


class ResultItem(StrictModel):
    kind: Literal["repository_record"] = "repository_record"
    data: RepositoryRecord


class RepositoryAnalyzeResult(StrictModel):
    schemaVersion: Literal["0.2"] = "0.2"
    subtaskId: str
    taskType: Literal["repository_analyze"] = "repository_analyze"
    summary: str
    items: list[ResultItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EnrichmentPayload(StrictModel):
    overview: Overview | None = None
    reproductionBarriers: list[ReproductionBarrier] = Field(default_factory=list)


JsonObject = dict[str, Any]
