"""Local AIP/ACPS SDK adapter for ResearchMesh Paper Reader business v0.2.

Isolated from the frozen v0.1 wu_delivery contracts and partner.
Only an in-process ASGI transport is supported by the accompanying demo.
No ACS registration, identity verification, ADP or MQ implementation here.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from acps_sdk.aip import Product, StructuredDataItem, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers, DefaultHandlers, TaskManager, add_aip_rpc_router,
)
from fastapi import FastAPI
from pydantic import ValidationError

from agents.paper_reader.researchmesh_paper_reader.agent import PaperReaderAgent
from agents.paper_reader.researchmesh_paper_reader.schemas import PaperReadResult, PaperReadTask
from aip.wu_delivery.adapter import normalize_state
from result_contract import validate_result

BUSINESS_FIELDS = (
    "schemaVersion", "subtaskId", "taskType", "goal", "input",
    "constraints", "outputSchema",
)


def business_task_from_planner(task: dict[str, Any]) -> PaperReadTask:
    """Strip Leader-only fields, then validate the seven-field v0.2 payload."""
    if task.get("requiredSkill") not in (None, "paper.analyze"):
        raise ValueError("SKILL_MISMATCH")
    if task.get("dependsOn") not in (None, []):
        raise ValueError("DEPENDENCIES_NOT_EXECUTED")
    if any(key not in task for key in BUSINESS_FIELDS):
        raise ValueError("MISSING_BUSINESS_FIELD")
    return PaperReadTask.model_validate({key: task[key] for key in BUSINESS_FIELDS})


def validate_reader_result(raw: Any, task: PaperReadTask) -> dict[str, Any]:
    """Revalidate structure, correlation and Leader's actual v0.2 requirements."""
    if isinstance(raw, PaperReadResult):
        raw = raw.model_dump(mode="json")
    result = PaperReadResult.model_validate(raw)
    data = result.model_dump(mode="json")
    request = task.model_dump(mode="json")
    validate_result(data, request)
    return data


def create_reader_local_app(agent: PaperReaderAgent, *, backend_timeout_seconds: float = 120.0) -> FastAPI:
    """Create a local Partner, keeping the old wu_delivery Partner unchanged."""
    if not math.isfinite(backend_timeout_seconds) or backend_timeout_seconds <= 0:
        raise ValueError("INVALID_TIMEOUT")
    app = FastAPI(title="ResearchMesh local v0.2 Paper Reader AIP")

    async def start(command: Any, existing: Any) -> Any:
        if existing is not None:
            return existing
        structured = [x for x in (command.dataItems or []) if isinstance(x, StructuredDataItem)]
        try:
            if len(structured) != 1 or len(command.dataItems or []) != 1:
                raise ValueError("EXPECTED_ONE_STRUCTURED_TASK")
            request = PaperReadTask.model_validate(structured[0].data)
        except (ValidationError, ValueError, TypeError):
            rejected = TaskManager.create_task(
                command, initial_state=TaskState.Rejected,
                data_items=[TextDataItem(text="INVALID_PAPER_READ_V02_TASK")],
            )
            rejected.senderId = "local-paper-reader-partner"
            return rejected

        task = TaskManager.create_task(command, initial_state=TaskState.Working)
        task.senderId = "local-paper-reader-partner"
        try:
            async with asyncio.timeout(backend_timeout_seconds):
                result = await agent.run(request)
            validated = validate_reader_result(result, request)
        except TimeoutError:
            failure = "READER_BACKEND_TIMEOUT"
        except (ValidationError, ValueError, TypeError):
            failure = "READER_INVALID_RESULT"
        except Exception:
            # Do not expose upstream exception text: could contain a credential/URL.
            failure = "READER_BACKEND_ERROR"
        else:
            if task.status.state != TaskState.Working:
                return TaskManager.get_task(task.taskId)
            TaskManager.set_products(task.taskId, [Product(
                id=f"reader-result-{task.taskId}", name="paper-notes-v1",
                dataItems=[StructuredDataItem(data=validated)],
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
class ReaderAipOutcome:
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
            "taskId": self.task_id,
            "sessionId": self.session_id,
            "status": self.status,
            "protocolCompleted": self.protocol_completed,
            "businessValidated": self.business_validated,
            "researchSucceeded": self.research_succeeded,
            "backendMode": self.backend_mode,
            "result": self.result,
            "errorCode": self.error_code,
        }


async def invoke_reader_aip(
    client: Any, request: PaperReadTask, *, timeout_seconds: float = 150.0,
    backend_mode: str = "fixture",
) -> ReaderAipOutcome:
    """Official SDK TaskCommand/TaskResult round trip with explicit v0.2 validation.

    ResearchClient.start_research accepts a model via model_dump(); hence the
    v0.2 Pydantic task can be passed without using the v0.1 ResearchTask class.
    """
    if backend_mode not in ("fixture", "real"):
        raise ValueError("INVALID_BACKEND_MODE")
    session_id, task_id = str(uuid4()), str(uuid4())
    status = "unknown"
    result: dict[str, Any] | None = None
    validated = False
    protocol = False
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await client.start_research(request, session_id=session_id, task_id=task_id)
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
                result = validate_reader_result(structured[0].data, request)
                validated = True
                if status == "awaiting-completion":
                    confirm = await client.complete_task(task_id=task_id, session_id=session_id)
                    if confirm.taskId != task_id or confirm.sessionId != session_id:
                        raise ValueError("COMPLETE_CORRELATION_MISMATCH")
                    status = normalize_state(confirm.status.state)
                protocol = status == "completed"
                if not protocol:
                    error = "AIP_COMPLETION_FAILED"
    except TimeoutError:
        error, status = "AIP_TIMEOUT", "timeout"
    except (ValidationError, ValueError, TypeError, KeyError):
        error = "INVALID_READER_AIP_RESULT"
    except Exception:
        error = "AIP_TRANSPORT_ERROR"

    warnings = set(result.get("warnings") or []) if result else set()
    success = bool(
        backend_mode == "real" and protocol and validated and error is None and result
        and result["items"] and not {"FULL_TEXT_UNAVAILABLE", "PAPERQA_STRUCTURED_OUTPUT_INCOMPLETE"}.intersection(warnings)
        and all(x["data"].get("analysisLevel") == "fulltext" for x in result["items"])
    )
    return ReaderAipOutcome(
        task_id=task_id, session_id=session_id, status=status,
        protocol_completed=protocol, business_validated=validated,
        research_succeeded=success, backend_mode=backend_mode, result=result, error_code=error,
    )
