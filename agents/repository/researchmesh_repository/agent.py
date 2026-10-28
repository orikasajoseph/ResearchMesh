from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .codewiki_provider import CodeWikiProvider, CodeWikiStaticResult, StaticAnalysisError
from .config import (
    CODEWIKI_COMMIT,
    CODEWIKI_VERSION,
    RepositoryAgentConfig,
)
from .enrichment import EnrichmentResult, RepositoryEnricher
from .evidence import RepositoryEvidenceCollector, extract_declared_metadata
from .fetcher import (
    FetchedRepository,
    InvalidRepositoryUrl,
    RepositoryCloneError,
    RepositoryFetcher,
    validate_repository_url,
)
from .schemas import (
    AnalysisCounts,
    AnalysisMetrics,
    Dependency,
    Diagnostics,
    Entrypoint,
    Evidence,
    RepositoryAnalyzeRequest,
    RepositoryAnalyzeResult,
    RepositoryIdentity,
    RepositoryRecord,
    ResultItem,
    RunCommand,
    StructureComponent,
    StructureFile,
)


class RepositoryAgent:
    def __init__(
        self,
        *,
        config: RepositoryAgentConfig | None = None,
        fetcher: RepositoryFetcher | None = None,
        codewiki_provider: CodeWikiProvider | None = None,
        evidence_collector: RepositoryEvidenceCollector | None = None,
        enricher: RepositoryEnricher | None = None,
        enrichment_enabled: bool = True,
    ) -> None:
        self.config = config or RepositoryAgentConfig()
        self.fetcher = fetcher or RepositoryFetcher(self.config)
        self._codewiki_provider = codewiki_provider
        self.evidence_collector = evidence_collector or RepositoryEvidenceCollector(
            self.config.max_text_file_bytes
        )
        self.enricher = enricher
        self.enrichment_enabled = enrichment_enabled
        self.git_diagnostics: list[dict] = []

    def analyze(
        self, request: RepositoryAnalyzeRequest | dict[str, Any]
    ) -> RepositoryAnalyzeResult:
        task = (
            request
            if isinstance(request, RepositoryAnalyzeRequest)
            else RepositoryAnalyzeRequest.model_validate(request)
        )
        started = time.perf_counter()
        urls = task.input.repositoryUrls
        if not urls:
            return self._failure(task, "REPOSITORY_UNAVAILABLE", "No repository URL was provided")

        warnings: list[str] = []
        if len(urls) > 1:
            warnings.append(
                "MULTIPLE_REPOSITORIES_IGNORED: v0.1 analyzes only the first repository URL"
            )
        try:
            selected_url = validate_repository_url(urls[0])
        except InvalidRepositoryUrl as exc:
            return self._failure(task, "INVALID_REPOSITORY_URL", str(exc))

        try:
            with self.fetcher.fetch(selected_url) as fetched:
                result = self._analyze_repository(task, fetched, warnings, started)
        except RepositoryCloneError as exc:
            self.git_diagnostics = list(getattr(self.fetcher, "git_diagnostics", exc.diagnostics))
            return self._failure(task, "REPOSITORY_CLONE_FAILED", str(exc))
        self.git_diagnostics = list(getattr(self.fetcher, "git_diagnostics", []))
        return result

    def analyze_local(
        self,
        request: RepositoryAnalyzeRequest | dict[str, Any],
        repo_path: str | Path,
        *,
        repository_url: str = "https://local.invalid/repository.git",
        commit_hash: str = "LOCAL_WORKTREE",
    ) -> RepositoryAnalyzeResult:
        """Development/test entry point; local paths are not part of Partner schema."""
        task = (
            request
            if isinstance(request, RepositoryAnalyzeRequest)
            else RepositoryAnalyzeRequest.model_validate(request)
        )
        started = time.perf_counter()
        fetched = FetchedRepository(
            path=Path(repo_path).resolve(),
            origin_url=repository_url,
            commit_hash=commit_hash,
            clone_ms=0.0,
        )
        return self._analyze_repository(task, fetched, [], started)

    def _analyze_repository(
        self,
        task: RepositoryAnalyzeRequest,
        fetched: FetchedRepository,
        warnings: list[str],
        started: float,
    ) -> RepositoryAnalyzeResult:
        try:
            static = self._provider().analyze(fetched.path)
        except StaticAnalysisError as exc:
            return self._failure(task, "STATIC_ANALYSIS_FAILED", str(exc))

        (
            structure,
            dependencies,
            component_evidence,
            artifact_entrypoints,
            artifact_commands,
            artifact_count,
        ) = _map_static_analysis(static)

        collected = self.evidence_collector.collect(
            fetched.path, start_index=len(component_evidence) + 1
        )
        warnings.extend(collected.warnings)
        all_evidence = [*component_evidence, *collected.evidence]
        file_entrypoints, file_commands = extract_declared_metadata(collected.evidence)
        entrypoints = _unique_entrypoints([*artifact_entrypoints, *file_entrypoints])
        run_commands = _unique_commands([*artifact_commands, *file_commands])

        enrichment = self._enrich(all_evidence, entrypoints, run_commands, dependencies,
                                  task.input.paperNotes)
        warnings.extend(enrichment.warnings)
        total_ms = round((time.perf_counter() - started) * 1000, 3)
        record = RepositoryRecord(
            repository=RepositoryIdentity(
                url=fetched.origin_url, commitHash=fetched.commit_hash
            ),
            structure=structure,
            dependencies=dependencies,
            entrypoints=entrypoints,
            runCommands=run_commands,
            reproductionBarriers=enrichment.reproduction_barriers,
            overview=enrichment.overview,
            evidence=all_evidence,
            paperEvidence=[item for note in task.input.paperNotes for item in note.evidence],
            paperCodeRelations=enrichment.paper_code_relations,
            diagnostics=Diagnostics(
                codewikiVersion=CODEWIKI_VERSION,
                codewikiCommit=CODEWIKI_COMMIT,
                metrics=AnalysisMetrics(
                    cloneMs=fetched.clone_ms,
                    staticAnalysisMs=static.static_analysis_ms,
                    llmEnrichmentMs=enrichment.latency_ms,
                    totalMs=total_ms,
                ),
                counts=AnalysisCounts(
                    componentCount=len(static.components),
                    dependencyEdgeCount=len(dependencies),
                    artifactCount=artifact_count,
                    evidenceCount=len(all_evidence),
                ),
            ),
        )
        return RepositoryAnalyzeResult(
            subtaskId=task.subtaskId,
            summary=(
                f"Analyzed one repository: {len(static.components)} components, "
                f"{len(dependencies)} dependency edges, {artifact_count} artifacts."
            ),
            items=[ResultItem(data=record)],
            warnings=list(dict.fromkeys(warnings)),
        )

    def _provider(self) -> CodeWikiProvider:
        if self._codewiki_provider is None:
            self._codewiki_provider = CodeWikiProvider()
        return self._codewiki_provider

    def _enrich(
        self,
        evidence: list[Evidence],
        entrypoints: list[Entrypoint],
        run_commands: list[RunCommand],
        dependencies: list[Dependency],
        paper_notes,
    ) -> EnrichmentResult:
        if not self.enrichment_enabled:
            return EnrichmentResult(None, [], [], 0.0)
        enricher = self.enricher
        if enricher is None:
            try:
                from common.llm.researchmesh_llm import LLMProvider

                enricher = RepositoryEnricher(
                    LLMProvider(), self.config.max_enrichment_evidence_chars
                )
            except Exception:  # noqa: BLE001 - static result remains useful
                return EnrichmentResult(
                    None, [], ["LLM_ENRICHMENT_UNAVAILABLE"], 0.0
                )
        kwargs = {"entrypoints": [item.model_dump() for item in entrypoints],
                  "run_commands": [item.model_dump() for item in run_commands],
                  "dependency_summary": {"edgeCount": len(dependencies)}}
        if paper_notes:
            kwargs["paper_notes"] = paper_notes
        return enricher.enrich(evidence, **kwargs)

    @staticmethod
    def _failure(
        task: RepositoryAnalyzeRequest, code: str, message: str
    ) -> RepositoryAnalyzeResult:
        return RepositoryAnalyzeResult(
            subtaskId=task.subtaskId,
            summary=f"Repository analysis failed: {code}",
            warnings=[f"{code}: {message}"],
        )


def _map_static_analysis(
    static: CodeWikiStaticResult,
) -> tuple[
    list[StructureFile],
    list[Dependency],
    list[Evidence],
    list[Entrypoint],
    list[RunCommand],
    int,
]:
    component_ids = sorted(static.components)
    involved = {
        component_id
        for component_id, node in static.components.items()
        if node.get("depends_on")
    }
    for node in static.components.values():
        involved.update(node.get("depends_on") or [])

    evidence: list[Evidence] = []
    evidence_by_component: dict[str, str] = {}
    files: dict[str, list[StructureComponent]] = {}
    entrypoints: list[Entrypoint] = []
    commands: list[RunCommand] = []
    artifact_count = 0

    for index, component_id in enumerate(component_ids, start=1):
        node = static.components[component_id]
        evidence_id = f"E{index}"
        evidence_by_component[component_id] = evidence_id
        relative = str(node.get("relative_path") or component_id.split("::", 1)[0]).replace(
            "\\", "/"
        )
        component_type = str(node.get("component_type") or "unknown")
        is_artifact = component_type == "artifact"
        artifact_count += int(is_artifact)
        supports = ["structure"]
        if component_id in involved:
            supports.append("dependency")
        if is_artifact:
            supports.extend(["entrypoints", "runCommands", "reproductionBarriers"])
        evidence.append(
            Evidence(
                evidenceId=evidence_id,
                sourceType="artifact" if is_artifact else "code_component",
                filePath=relative,
                symbol=str(node.get("name") or component_id),
                startLine=_positive_int(node.get("start_line")),
                endLine=_positive_int(node.get("end_line")),
                text=str(node.get("source_code") or ""),
                supports=list(dict.fromkeys(supports)),
            )
        )
        files.setdefault(relative, []).append(
            StructureComponent(
                componentId=component_id,
                name=str(node.get("name") or component_id),
                componentType=component_type,
                symbol=str(node.get("qualified_name") or node.get("name") or component_id),
                startLine=_positive_int(node.get("start_line")),
                endLine=_positive_int(node.get("end_line")),
                evidenceId=evidence_id,
            )
        )
        if is_artifact and node.get("node_type") == "artifact_unit":
            artifact_entrypoint = _entrypoint_from_artifact(node, evidence_id, relative)
            if artifact_entrypoint is not None:
                entrypoints.append(artifact_entrypoint)
                command = _command_from_entrypoint(artifact_entrypoint)
                if command is not None:
                    commands.append(command)

    dependencies: list[Dependency] = []
    for source in component_ids:
        targets = sorted(static.components[source].get("depends_on") or [])
        for target in targets:
            ids = [evidence_by_component[source]]
            if target in evidence_by_component:
                ids.append(evidence_by_component[target])
            dependencies.append(
                Dependency(source=source, target=target, evidenceIds=list(dict.fromkeys(ids)))
            )

    structure = [
        StructureFile(filePath=path, components=files[path]) for path in sorted(files)
    ]
    indexed_artifacts = static.artifact_index.get("nodes")
    if isinstance(indexed_artifacts, int):
        artifact_count = max(artifact_count, indexed_artifacts)
    return structure, dependencies, evidence, entrypoints, commands, artifact_count


def _entrypoint_from_artifact(
    node: dict[str, Any], evidence_id: str, relative: str
) -> Entrypoint | None:
    source = str(node.get("source_code") or "").strip()
    name = str(node.get("name") or "").strip()
    if not source or not name:
        return None
    target = source.split("=", 1)[1].strip().strip("\"'") if "=" in source else source
    lower = relative.lower()
    if lower == "pyproject.toml":
        kind = "pyproject-script"
    elif lower == "package.json":
        kind = "package-script"
    elif lower in {"setup.py", "setup.cfg"}:
        kind = "console-script"
    else:
        kind = "artifact-entrypoint"
    return Entrypoint(
        name=name,
        entrypointType=kind,
        target=target,
        source=relative,
        evidenceIds=[evidence_id],
    )


def _command_from_entrypoint(entrypoint: Entrypoint) -> RunCommand | None:
    if entrypoint.entrypointType in {"pyproject-script", "poetry-script", "console-script"}:
        command = entrypoint.name
    elif entrypoint.entrypointType == "package-script":
        command = f"npm run {entrypoint.name}"
    else:
        return None
    return RunCommand(
        command=command,
        source=entrypoint.source,
        evidenceIds=entrypoint.evidenceIds,
    )


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 1 else None


def _unique_entrypoints(values: list[Entrypoint]) -> list[Entrypoint]:
    unique: dict[tuple[str, str, str, str], Entrypoint] = {}
    for value in values:
        unique.setdefault(
            (value.name, value.entrypointType, value.target, value.source), value
        )
    return list(unique.values())


def _unique_commands(values: list[RunCommand]) -> list[RunCommand]:
    unique: dict[tuple[str, str], RunCommand] = {}
    for value in values:
        unique.setdefault((value.command, value.source), value)
    return list(unique.values())
