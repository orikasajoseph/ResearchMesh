from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .config import CODEWIKI_VERSION


class StaticAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodeWikiStaticResult:
    components: dict[str, dict[str, Any]]
    component_index: list[dict[str, Any]]
    artifact_index: dict[str, Any]
    static_analysis_ms: float


class CodeWikiProvider:
    """Narrow adapter over CodeWiki's no-LLM MCP/static analysis handler."""

    def __init__(self) -> None:
        try:
            installed = version("codewiki")
        except PackageNotFoundError as exc:
            raise StaticAnalysisError("CodeWiki is not installed") from exc
        if installed != CODEWIKI_VERSION:
            raise StaticAnalysisError(
                f"CodeWiki {CODEWIKI_VERSION} is required; found {installed}"
            )

    def analyze(self, repo_path: Path) -> CodeWikiStaticResult:
        try:
            from codewiki.mcp.session import SessionStore
            from codewiki.mcp.tools.analysis import handle_analyze_repo
        except Exception as exc:  # noqa: BLE001 - converted at provider boundary
            raise StaticAnalysisError(f"Unable to load CodeWiki static handler: {exc}") from exc

        root = repo_path.resolve()
        if not root.is_dir():
            raise StaticAnalysisError(f"Repository directory does not exist: {root}")

        started = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="researchmesh_codewiki_") as output:
                store = SessionStore()
                raw = handle_analyze_repo(
                    {
                        "repo_path": str(root),
                        "output_dir": output,
                        "use_gitignore": True,
                    },
                    store,
                )
                response = json.loads(raw)
                if response.get("error"):
                    raise StaticAnalysisError(str(response["error"]))

                component_path = Path(response["files"]["component_index"])
                component_index = _read_json(component_path)
                if not isinstance(component_index, list):
                    raise StaticAnalysisError("CodeWiki component_index.json is not a list")

                graph_dir = Path(output) / "temp" / "dependency_graphs"
                graphs = sorted(graph_dir.glob("*_dependency_graph.json"))
                if len(graphs) != 1:
                    raise StaticAnalysisError(
                        f"Expected one CodeWiki dependency graph, found {len(graphs)}"
                    )
                components = _read_json(graphs[0])
                if not isinstance(components, dict):
                    raise StaticAnalysisError("CodeWiki dependency graph is not an object")

                indexed_ids = {
                    item.get("id") for item in component_index if isinstance(item, dict)
                }
                if indexed_ids != set(components):
                    raise StaticAnalysisError(
                        "CodeWiki component index and dependency graph disagree"
                    )

                artifact_path = Path(output) / "temp" / "artifact_index.json"
                artifact_index = _read_json(artifact_path) if artifact_path.exists() else {}
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                return CodeWikiStaticResult(
                    components=components,
                    component_index=component_index,
                    artifact_index=artifact_index,
                    static_analysis_ms=elapsed,
                )
        except StaticAnalysisError:
            raise
        except Exception as exc:  # noqa: BLE001 - stable domain error for callers
            raise StaticAnalysisError(f"CodeWiki static analysis failed: {exc}") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticAnalysisError(f"Unable to read CodeWiki artifact {path.name}: {exc}") from exc
