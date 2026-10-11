"""Planner-facing AIP v0.1 adapter (SDK-model shaped input, no network imports).

Converts official TaskResult data/status to an explicit PlannerOutcome. It does
NOT perform ADP discovery or claim that schema-valid outputs are real research.
Use an installed SDK 2.2.0 ResearchClient for live RPC operations.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter
from typing import Any

from .contracts import ResearchTask, ResearchResult


EXPECTED_KIND = {
    "literature_search": "paper",
    "paper_read": "paper_analysis",
    "code_search": "code",
}
PENDING_STATES = frozenset({"accepted", "working", "awaiting-input"})
FAILED_STATES = frozenset({"failed", "rejected", "canceled", "cancelled"})
RESULT_STATES = frozenset({"awaiting-completion", "completed"})


@dataclass
class PlannerOutcome:
    subtask_id: str
    task_id: str
    session_id: str
    status: str
    latency_ms: float
    result: ResearchResult | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    protocol_completed: bool = False
    business_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serializable shape for logs and for the Planner."""
        return {
            "subtaskId": self.subtask_id,
            "taskId": self.task_id,
            "sessionId": self.session_id,
            "status": self.status,
            "latencyMs": round(self.latency_ms, 2),
            "result": self.result.model_dump() if self.result is not None else None,
            "error": self.error,
            "warnings": self.warnings,
            "protocolCompleted": self.protocol_completed,
            "businessReady": self.business_ready,
        }


def normalize_state(value: Any) -> str:
    """SDK enum value -> protocol wire state; unknown states must not be success."""
    if isinstance(value, Enum):
        value = value.value
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized.startswith("taskstate."):
        normalized = normalized[len("taskstate."):]
    if normalized == "awaitingcompletion":
        normalized = "awaiting-completion"
    if normalized == "awaitinginput":
        normalized = "awaiting-input"
    return normalized


def result_from_products(task_result: Any, original_request: ResearchTask) -> ResearchResult:
    """Validate exactly one structured result, including correlation and item kinds."""
    data_items = [
        item for product in (getattr(task_result, "products", None) or [])
        for item in (getattr(product, "dataItems", None) or [])
        if getattr(item, "type", "data") == "data" and isinstance(getattr(item, "data", None), dict)
    ]
    if len(data_items) != 1:
        raise ValueError(f"Expected 1 StructuredDataItem, got {len(data_items)}")
    result = ResearchResult.model_validate(data_items[0].data)
    if result.subtaskId != original_request.subtaskId or result.taskType != original_request.taskType:
        raise ValueError("ResearchResult subtaskId/taskType mismatch")
    expected = EXPECTED_KIND[original_request.taskType]
    if any(item.kind != expected for item in result.items):
        raise ValueError(f"ResearchResult item.kind must be {expected!r}")
    return result


def _failure_details(task_result: Any) -> str:
    parts = []
    for item in (getattr(getattr(task_result, "status", None), "dataItems", None) or []):
        value = getattr(item, "text", None)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "; ".join(parts)[:400]  # avoid flooding logs


async def invoke_for_planner(
    client: Any,
    request: ResearchTask,
    *,
    session_id: str,
    task_id: str,
    timeout_seconds: float = 30.0,
) -> PlannerOutcome:
    """Start an AIP task, validate its Product, and acknowledge completion.

    AIP terminal protocol state and research correctness are separate concepts.
    Awaiting-input/working are returned as pending, not incorrectly completed.
    """
    start = perf_counter()
    outcome = PlannerOutcome(request.subtaskId, task_id, session_id, "unknown", 0.0)
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await client.start_research(request, session_id=session_id, task_id=task_id)
            if getattr(response, "taskId", None) != task_id:
                raise ValueError("AIP TaskResult.taskId mismatch")
            state = normalize_state(response.status.state)
            outcome.status = state
            if state in FAILED_STATES:
                outcome.error = f"AIP_{state.upper().replace('-', '_')}: {_failure_details(response)}".rstrip(": ")
            elif state in PENDING_STATES:
                outcome.warnings.append(f"AIP_PENDING: {state}; do not treat as completed")
            elif state in RESULT_STATES:
                business = result_from_products(response, request)
                outcome.result = business
                outcome.warnings.extend(business.warnings)
                is_transport_test = any(w.startswith("TRANSPORT_TEST_ONLY") for w in business.warnings)
                outcome.business_ready = not is_transport_test
                if state == "awaiting-completion":
                    confirm = await client.complete_task(task_id=task_id, session_id=session_id)
                    if getattr(confirm, "taskId", None) != task_id:
                        raise ValueError("AIP complete response.taskId mismatch")
                    confirmed_state = normalize_state(confirm.status.state)
                    outcome.status = confirmed_state
                    if confirmed_state != "completed":
                        outcome.error = f"Unexpected state after complete: {confirmed_state}"
                        outcome.business_ready = False
                outcome.protocol_completed = outcome.status == "completed"
            else:
                outcome.error = f"UNKNOWN_AIP_STATE: {state}"
    except TimeoutError:
        outcome.status = "timeout"
        outcome.error = "AIP_TIMEOUT: wait for task result exceeded configured timeout"
        outcome.business_ready = False
    except (ValueError, TypeError) as exc:
        outcome.error = f"INVALID_RESULT: {exc}"
        outcome.business_ready = False
    except Exception as exc:
        # Keep SDK/transport failures distinguishable from valid negative task results.
        # Do not echo exception message: it might contain URLs or credentials.
        outcome.error = f"AIP_TRANSPORT_ERROR: {type(exc).__name__}"
        outcome.business_ready = False
    finally:
        outcome.latency_ms = (perf_counter() - start) * 1000
    return outcome
