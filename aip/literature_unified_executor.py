"""Adapt the verified Literature backend to Zhao's seven/six-field boundary.

This module owns no AIC, Group, MQ or SQLite state. Importing it is offline.
A caller must durably reserve the Group task before invoking this executor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aip.wu_delivery.contracts import ResearchResult as LegacyResult
from aip.wu_delivery.contracts import ResearchTask as LegacyTask
from business_execution import execute_business_task
from run_literature import execute_live_task


ROOT = Path(__file__).resolve().parents[1]
Backend = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class LiteratureProviderError(RuntimeError):
    """Only fixed error codes; never expose provider messages or secrets."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def live_literature_backend(payload: dict[str, Any]) -> dict[str, Any]:
    """Call the existing Zhang Literature pipeline without automatic retries."""
    outcome = await execute_live_task(
        payload,
        ROOT / "agents" / "literature",
        request_source="literature-agent",
        retry=False,
        progress=lambda *_args, **_kwargs: None,
    )
    if not isinstance(outcome, dict):
        raise LiteratureProviderError("BACKEND_UNAVAILABLE")
    if outcome.get("error") is not None:
        error = outcome["error"]
        code = error.get("code") if isinstance(error, dict) else None
        allowed = {
            "INVALID_REQUEST",
            "RATE_LIMITED",
            "PROVIDER_UNAVAILABLE",
            "NO_RESULTS",
            "BACKEND_UNAVAILABLE",
        }
        raise LiteratureProviderError(
            code if code in allowed else "BACKEND_UNAVAILABLE"
        )
    result = outcome.get("result")
    if not isinstance(result, dict):
        raise LiteratureProviderError("BACKEND_UNAVAILABLE")
    return result


async def execute_unified_literature(
    task: LegacyTask,
    *,
    backend: Backend | None = None,
    timeout_seconds: float = 110.0,
) -> LegacyResult:
    """Handler-compatible executor; v0.1 is internal, not a new wire format.

    Group Handler provides a LegacyTask only after strict v0.2 validation
    and durable claim. Zhao's entry validates and executes v0.2 business JSON.
    The returned six-field result is changed back to v0.1 only because the
    existing Handler performs its own final v0.2 conversion and checks.
    """
    if task.taskType != "literature_search":
        raise ValueError("LITERATURE_ONLY")

    business_task = task.model_dump(mode="json")
    business_task["schemaVersion"] = "0.2"

    result = await execute_business_task(
        business_task,
        skill="literature.search",
        backend=backend if backend is not None else live_literature_backend,
        allow_execution=True,
        timeout_seconds=timeout_seconds,
    )

    legacy_result = dict(result)
    legacy_result["schemaVersion"] = "0.1"
    return LegacyResult.model_validate(legacy_result)
