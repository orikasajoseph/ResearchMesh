"""ResearchMesh local Repository Agent v0.2 AIP bridge.

Uses the same official SDK TaskCommand/TaskResult RPC lifecycle as the tested
Paper Reader bridge. LOCAL ASGI ONLY; no ACS, ADP, MQ or remote identity binding.

Repository business output is adapted, not mutated. Evidence links are built
only for real GitHub clones with a concrete 40-hex Git commit, and cannot be
assumed verified merely because they were constructed.
"""
from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from acps_sdk.aip import Product, StructuredDataItem, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers, DefaultHandlers, TaskManager, add_aip_rpc_router,
)
from fastapi import FastAPI
from pydantic import ValidationError

from agents.repository.researchmesh_repository.schemas import (
    RepositoryAnalyzeRequest, RepositoryAnalyzeResult,
)
from aip.wu_delivery.adapter import normalize_state
from result_contract import validate_result

BUSINESS_FIELDS = (
    "schemaVersion", "subtaskId", "taskType", "goal", "input",
    "constraints", "outputSchema",
)
_NON_RESEARCH_WARNINGS = {"REPOSITORY_UNAVAILABLE", "INVALID_REPOSITORY_URL",
                          "REPOSITORY_CLONE_FAILED", "STATIC_ANALYSIS_FAILED"}
_SHA40 = re.compile(r"[0-9a-fA-F]{40}\Z")
_GH_OWNER = re.compile(r"[A-Za-z0-9-]{1,39}\Z")
_GH_REPO = re.compile(r"[A-Za-z0-9_.-]+\Z")


class RepositoryEvidenceError(ValueError):
    """A source locator cannot be safely converted to a permalink."""


def business_task_from_planner(task: dict[str, Any]) -> RepositoryAnalyzeRequest:
    """Drop verified Leader-only fields and unsupported-but-false constraint flag."""
    if not isinstance(task, dict):
        raise ValueError("INVALID_PLANNER_TASK")
    if task.get("requiredSkill") not in (None, "repository.analyze"):
        raise ValueError("SKILL_MISMATCH")
    if task.get("dependsOn") not in (None, []):
        raise ValueError("DEPENDENCIES_NOT_EXECUTED")
    if any(k not in task for k in BUSINESS_FIELDS):
        raise ValueError("MISSING_BUSINESS_FIELD")
    wire = {k: task[k] for k in BUSINESS_FIELDS}
    constraints = wire["constraints"]
    if not isinstance(constraints, dict):
        raise ValueError("INVALID_CONSTRAINTS")
    constraints = dict(constraints)
    if "allowRepositorySearch" in constraints:
        if constraints.pop("allowRepositorySearch") is not False:
            raise ValueError("REPOSITORY_SEARCH_FORBIDDEN")
    wire["constraints"] = constraints
    request = RepositoryAnalyzeRequest.model_validate(wire)
    if len(request.input.repositoryUrls) > 1:
        raise ValueError("MULTIPLE_REPOSITORIES_UNSUPPORTED")
    return request


def _github_blob_url(repo_url: str, commit: str, file_path: str,
                     start: int | None, end: int | None) -> str:
    """Construct a permalink ONLY for a verified-format GitHub source.

    A syntactically valid permalink is not network verification. The caller
    must obtain the URL and SHA from the actual clone, never from an LLM.
    """
    parts = urlsplit(repo_url)
    if (parts.scheme != "https" or parts.hostname != "github.com"
            or parts.port not in (None, 443) or parts.username or parts.password
            or parts.query or parts.fragment):
        raise RepositoryEvidenceError("UNSUPPORTED_REPOSITORY_EVIDENCE_HOST")
    components = parts.path.strip("/").split("/")
    if len(components) != 2:
        raise RepositoryEvidenceError("INVALID_GITHUB_REPOSITORY_PATH")
    owner, name = components
    if name.endswith(".git"):
        name = name[:-4]
    if not _GH_OWNER.fullmatch(owner) or not _GH_REPO.fullmatch(name) or name in {".", ".."}:
        raise RepositoryEvidenceError("INVALID_GITHUB_REPOSITORY_PATH")
    if not isinstance(commit, str) or not _SHA40.fullmatch(commit):
        raise RepositoryEvidenceError("INVALID_REPOSITORY_COMMIT")
    if not isinstance(file_path, str) or not file_path:
        raise RepositoryEvidenceError("INVALID_EVIDENCE_PATH")
    # No filesystem traversal, UNC, drive letters, absolute path, or percent-escapes.
    path = file_path.replace("\\", "/")
    if (path.startswith("/") or ":" in path or "%" in path or "?" in path
            or "#" in path or "//" in path or any(seg in {"", ".", ".."}
                                              for seg in path.split("/"))
            or any(ord(ch) < 32 for ch in path)):
        raise RepositoryEvidenceError("INVALID_EVIDENCE_PATH")
    anchor = ""
    if start is not None:
        if not isinstance(start, int) or start < 1:
            raise RepositoryEvidenceError("INVALID_LINE_RANGE")
        anchor = f"#L{start}"
        if end is not None:
            if not isinstance(end, int) or end < start:
                raise RepositoryEvidenceError("INVALID_LINE_RANGE")
            if end > start:
                anchor += f"-L{end}"
    elif end is not None:
        raise RepositoryEvidenceError("INVALID_LINE_RANGE")
    return f"https://github.com/{owner}/{name}/blob/{commit}/{quote(path, safe='/')}{anchor}"


def _evidence_for_leader(record: dict[str, Any]) -> list[dict[str, str]]:
    identity = record["repository"]
    results: list[dict[str, str]] = []
    for e in record["evidence"]:
        source_id = e["evidenceId"]
        path = e["filePath"]
        if not isinstance(source_id, str) or not source_id.strip():
            raise RepositoryEvidenceError("INVALID_EVIDENCE_ID")
        url = _github_blob_url(identity["url"], identity["commitHash"], path,
                               e["startLine"], e["endLine"])
        if e["startLine"] is not None:
            locator = f"{path}:L{e['startLine']}"
            if e["endLine"] is not None and e["endLine"] > e["startLine"]:
                locator += f"-L{e['endLine']}"
        else:
            locator = path
        supports = [x.strip() for x in e["supports"] if isinstance(x, str) and x.strip()]
        # If CodeWiki didn't label supported claims, describe only direct file evidence.
        # Do NOT interpret code behavior or assert successful reproduction.
        supported = "; ".join(supports) if supports else (
            f"CodeWiki extracted a static source fragment from {locator}; "
            "execution and reproduction have not been verified."
        )
        results.append({"sourceId": source_id, "url": url,
                        "locator": locator, "supports": supported})
    return results


def normalize_repository_result(raw: Any, task: RepositoryAnalyzeRequest, *,
                                backend_mode: str = "fixture") -> dict[str, Any]:
    """Validate Zhang's native result then convert to Zhao's v0.2 ResearchResult.

    A fixture keeps structure but is always marked isMock=true and has no
    asserted web evidence. Real results must contain genuine traceable sources.
    """
    if backend_mode not in ("fixture", "real"):
        raise ValueError("INVALID_BACKEND_MODE")
    parsed = RepositoryAnalyzeResult.model_validate(raw)
    native = parsed.model_dump(mode="json")
    if (native["schemaVersion"] != task.schemaVersion
            or native["subtaskId"] != task.subtaskId
            or native["taskType"] != task.taskType):
        raise ValueError("RESULT_TASK_CORRELATION_MISMATCH")
    warnings = list(native["warnings"])
    items = []
    for item in native["items"]:
        record = item["data"]
        if not record["repository"]["url"].strip():
            raise ValueError("REPOSITORY_URL_MISSING")
        evidence = _evidence_for_leader(record) if backend_mode == "real" else []
        if backend_mode == "real" and not evidence:
            raise RepositoryEvidenceError("REPOSITORY_EVIDENCE_UNAVAILABLE")
        data = {
            "url": record["repository"]["url"],
            "license": "UNKNOWN",  # no license claim is exposed by Zhang's schema
            "relationEvidence": (
                "Only an explicitly supplied repository URL was analyzed; "
                "a link to any cited paper has not been independently verified."
            ),
            "isMock": backend_mode == "fixture",
            "repository": record["repository"],
            "structure": record["structure"],
            "dependencies": record["dependencies"],
            "entrypoints": record["entrypoints"],
            "runCommands": record["runCommands"],
            "reproductionBarriers": record["reproductionBarriers"],
            "overview": record["overview"],
            "diagnostics": record["diagnostics"],
            "evidence": evidence,
        }
        items.append({"kind": "repository", "data": data})
    if items:
        warnings.extend(["LICENSE_NOT_VERIFIED", "PAPER_REPOSITORY_RELATION_NOT_VERIFIED"])
    if backend_mode == "fixture":
        warnings.append("FIXTURE_ONLY_NO_REAL_CODEWIKI_ANALYSIS")
    normalized = {
        "schemaVersion": native["schemaVersion"],
        "subtaskId": native["subtaskId"],
        "taskType": native["taskType"],
        "summary": native["summary"],
        "items": items,
        "warnings": list(dict.fromkeys(warnings)),
    }
    validate_result(normalized, task.model_dump(mode="json"))
    return normalized


def create_repository_local_app(agent: Any, *, backend_mode: str = "fixture",
                                backend_timeout_seconds: float = 120.0) -> FastAPI:
    """Local Partner using official ACPS SDK RPC and a blocking repository backend."""
    if backend_mode not in ("fixture", "real"):
        raise ValueError("INVALID_BACKEND_MODE")
    if not math.isfinite(backend_timeout_seconds) or backend_timeout_seconds <= 0:
        raise ValueError("INVALID_TIMEOUT")
    app = FastAPI(title="ResearchMesh local v0.2 Repository AIP")

    async def start(command: Any, existing: Any) -> Any:
        if existing is not None:
            return existing
        structured = [x for x in (command.dataItems or []) if isinstance(x, StructuredDataItem)]
        try:
            if len(structured) != 1 or len(command.dataItems or []) != 1:
                raise ValueError("EXPECTED_ONE_STRUCTURED_TASK")
            request = RepositoryAnalyzeRequest.model_validate(structured[0].data)
            if len(request.input.repositoryUrls) > 1:
                raise ValueError("MULTIPLE_REPOSITORIES_UNSUPPORTED")
        except (ValidationError, ValueError, TypeError):
            rejected = TaskManager.create_task(
                command, initial_state=TaskState.Rejected,
                data_items=[TextDataItem(text="INVALID_REPOSITORY_V02_TASK")],
            )
            rejected.senderId = "local-repository-partner"
            return rejected

        task = TaskManager.create_task(command, initial_state=TaskState.Working)
        task.senderId = "local-repository-partner"
        try:
            # asyncio timeout doesn't forcibly stop an already running git clone
            # thread. Zhang's fetcher must enforce its own clone timeout as well.
            async with asyncio.timeout(backend_timeout_seconds):
                raw_result = await asyncio.to_thread(agent.analyze, request)
            normalized = normalize_repository_result(raw_result, request,
                                                      backend_mode=backend_mode)
        except TimeoutError:
            failure = "REPOSITORY_BACKEND_TIMEOUT"
        except (ValidationError, ValueError, TypeError):
            failure = "REPOSITORY_INVALID_RESULT"
        except Exception:
            # Avoid leaking environment variables, tokens or clone URLs.
            failure = "REPOSITORY_BACKEND_ERROR"
        else:
            if task.status.state != TaskState.Working:
                return TaskManager.get_task(task.taskId)
            TaskManager.set_products(task.taskId, [Product(
                id=f"repository-result-{task.taskId}", name="code-records-v1",
                dataItems=[StructuredDataItem(data=normalized)],
            )])
            TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
            return TaskManager.get_task(task.taskId)
        if task.status.state == TaskState.Working:
            TaskManager.update_task_status(task.taskId, TaskState.Failed,
                                           [TextDataItem(text=failure)])
        return TaskManager.get_task(task.taskId)

    add_aip_rpc_router(app, "/rpc", CommandHandlers(
        on_start=start, on_get=DefaultHandlers.get,
        on_complete=DefaultHandlers.complete, on_cancel=DefaultHandlers.cancel,
        on_continue=DefaultHandlers.continue_,
    ), identity_binding_enabled=False)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local-only", "version": "0.2"}

    return app


@dataclass(frozen=True)
class RepositoryAipOutcome:
    task_id: str
    session_id: str
    status: str
    protocol_completed: bool
    business_validated: bool
    research_succeeded: bool
    backend_mode: str
    result: dict[str, Any] | None
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id, "sessionId": self.session_id,
            "status": self.status, "protocolCompleted": self.protocol_completed,
            "businessValidated": self.business_validated,
            "researchSucceeded": self.research_succeeded,
            "backendMode": self.backend_mode, "result": self.result,
            "errorCode": self.error_code,
        }


async def invoke_repository_aip(client: Any, request: RepositoryAnalyzeRequest,
                                *, timeout_seconds: float = 150.0,
                                backend_mode: str = "fixture") -> RepositoryAipOutcome:
    """Official SDK start / complete sequence with result and ID correlation."""
    if backend_mode not in ("fixture", "real"):
        raise ValueError("INVALID_BACKEND_MODE")
    session_id, task_id = str(uuid4()), str(uuid4())
    status = "unknown"
    result: dict[str, Any] | None = None
    validated, protocol = False, False
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await client.start_research(request, session_id=session_id,
                                                   task_id=task_id)
            if response.taskId != task_id or response.sessionId != session_id:
                raise ValueError("TASK_CORRELATION_MISMATCH")
            status = normalize_state(response.status.state)
            if status not in {"awaiting-completion", "completed"}:
                error = f"AIP_STATE_{status.upper().replace('-', '_')}"
            else:
                structured = [
                    item for product in (response.products or [])
                    for item in (product.dataItems or [])
                    if isinstance(item, StructuredDataItem)
                ]
                if len(structured) != 1:
                    raise ValueError("STRUCTURED_RESULT_COUNT")
                result = structured[0].data
                # Product contains Zhao-normalized output, not Zhang native output.
                validate_result(result, request.model_dump(mode="json"))
                validated = True
                if status == "awaiting-completion":
                    confirmed = await client.complete_task(task_id=task_id,
                                                          session_id=session_id)
                    if confirmed.taskId != task_id or confirmed.sessionId != session_id:
                        raise ValueError("COMPLETE_CORRELATION_MISMATCH")
                    status = normalize_state(confirmed.status.state)
                protocol = status == "completed"
                if not protocol:
                    error = "AIP_COMPLETION_FAILED"
    except TimeoutError:
        error, status = "AIP_TIMEOUT", "timeout"
    except (ValidationError, ValueError, TypeError, KeyError):
        error = "INVALID_REPOSITORY_AIP_RESULT"
    except Exception:
        error = "AIP_TRANSPORT_ERROR"

    items = result.get("items") if isinstance(result, dict) else None
    warnings = result.get("warnings") if isinstance(result, dict) else None
    success = bool(
        backend_mode == "real" and protocol and validated and error is None
        and items and all(not x.get("data", {}).get("isMock", True)
                          and x.get("data", {}).get("evidence") for x in items)
        and not any(any(str(w).startswith(prefix) for prefix in _NON_RESEARCH_WARNINGS)
                    for w in (warnings or []))
    )
    return RepositoryAipOutcome(
        task_id=task_id, session_id=session_id, status=status,
        protocol_completed=protocol, business_validated=validated,
        research_succeeded=success, backend_mode=backend_mode,
        result=result, error_code=error,
    )
