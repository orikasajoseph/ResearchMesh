"""Bounded async backend execution without changing ResearchMesh wire schemas."""
from __future__ import annotations

import asyncio
import math
from typing import Awaitable, Callable

from pydantic import ValidationError

from .adapter import EXPECTED_KIND
from .contracts import ResearchTask, ResearchResult

ResearchExecutor = Callable[[ResearchTask], Awaitable[ResearchResult]]


class BackendFailure(Exception):
    """A stable, safe code for AIP status; never contains upstream exception text."""


def validate_timeout(seconds: float) -> None:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("backend timeout must be finite and positive")


async def execute_checked(
    execute: ResearchExecutor, task: ResearchTask, *, timeout_seconds: float,
) -> ResearchResult:
    """Validate a backend result and correlation before publishing a Product.

    execute must be nonblocking and cancellation-cooperative. This timeout cannot
    stop a blocking SDK, external job, or coroutine that suppresses cancellation.
    """
    validate_timeout(timeout_seconds)
    try:
        async with asyncio.timeout(timeout_seconds):
            raw = await execute(task.model_copy(deep=True))
    except TimeoutError as exc:
        raise BackendFailure("BACKEND_TIMEOUT") from exc
    except BackendFailure:
        # Preserve the safe, stable upstream failure code for the AIP status.
        raise
    except Exception as exc:
        raise BackendFailure(f"BACKEND_ERROR: {type(exc).__name__}") from exc

    try:
        # Revalidate even a constructed/mutated Pydantic model at the boundary.
        payload = raw.model_dump() if isinstance(raw, ResearchResult) else raw
        result = ResearchResult.model_validate(payload)
        if result.subtaskId != task.subtaskId or result.taskType != task.taskType:
            raise ValueError("backend correlation mismatch")
        if any(item.kind != EXPECTED_KIND[task.taskType] for item in result.items):
            raise ValueError("backend result kind mismatch")
    except (ValidationError, ValueError, TypeError) as exc:
        raise BackendFailure("BACKEND_INVALID_RESULT") from exc
    return result
