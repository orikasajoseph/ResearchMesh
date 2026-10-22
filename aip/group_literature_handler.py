"""Group TaskCommand -> Literature backend -> v0.2 business Result.

The formal Group Partner injects durable storage. No MQ is opened here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from acps_sdk.aip import Product, StructuredDataItem, TaskCommandType, TaskState
from business_execution import BusinessExecutionError
from aip.group_task_store import DurableTaskStore
from aip.literature_bridge import build_literature_request, execute_literature
from aip.wu_delivery.contracts import ResearchResult as LegacyResult
from aip.wu_delivery.contracts import ResearchTask as LegacyTask
from aip.wu_delivery.execution import BackendFailure
from common.models import BusinessTask
from input_binding import validate_task_input
from result_contract import validate_result

FIELDS = frozenset(BusinessTask.model_fields)
Executor = Callable[[LegacyTask], Awaitable[Any]]

# These codes are emitted before the unified executor invokes the backend.
# Only this known class of failures can safely be marked "failed" without
# treating an already-triggered upstream operation as retryable.
PRE_EXECUTION_ERROR_CODES = frozenset({
    "BUSINESS_EXECUTION_NOT_AUTHORIZED",
    "INVALID_BUSINESS_CONFIGURATION",
    "INVALID_BUSINESS_TASK",
    "BUSINESS_SKILL_MISMATCH",
    "INVALID_BUSINESS_TIMEOUT",
})

# Never forward arbitrary upstream error strings to the AIP Leader.
SAFE_BUSINESS_ERROR_CODES = PRE_EXECUTION_ERROR_CODES | frozenset({
    "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED",
    "INVALID_BUSINESS_RESULT",
    "BUSINESS_BACKEND_FAILED",
    "INVALID_REQUEST",
    "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "NO_RESULTS",
    "BACKEND_UNAVAILABLE",
})



@dataclass
class _TaskRecord:
    state: TaskState
    products: list[Product] | None = None


class LiteratureGroupHandler:
    def __init__(
        self,
        client: Any,
        *,
        leader_aic: str,
        executor: Executor = execute_literature,
        backend_timeout_seconds: float = 120.0,
        task_store: DurableTaskStore | None = None,
    ) -> None:
        if not leader_aic.strip() or backend_timeout_seconds <= 0:
            raise ValueError("LEADER_AIC_AND_TIMEOUT_REQUIRED")
        self.client = client
        self.leader_aic = leader_aic.strip()
        self.executor = executor
        self.timeout = backend_timeout_seconds
        self.task_store = task_store
        self._tasks: dict[tuple[str, str], _TaskRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def parse_business(command: Any) -> tuple[BusinessTask, LegacyTask]:
        items = command.dataItems or []
        if len(items) != 1 or not isinstance(items[0], StructuredDataItem):
            raise ValueError("EXPECTED_ONE_STRUCTURED_BUSINESS_ITEM")
        payload = items[0].data
        if not isinstance(payload, dict) or set(payload) != FIELDS:
            raise ValueError("EXACTLY_SEVEN_BUSINESS_FIELDS_REQUIRED")
        v02 = BusinessTask.model_validate(payload)
        if v02.taskType != "literature_search" or v02.outputSchema != "paper_records.v1":
            raise ValueError("LITERATURE_TASK_REQUIRED")
        validate_task_input(v02.model_dump())
        legacy_payload = v02.model_dump()
        legacy_payload["schemaVersion"] = "0.1"
        legacy = LegacyTask.model_validate(legacy_payload)
        build_literature_request(legacy)
        return v02, legacy

    def _advance(self, key: tuple[str, str], expected: tuple[str, ...], new_state: str) -> bool:
        if self.task_store is None:
            return True  # Compatibility only for offline fake-client tests.
        session_id, task_id = key
        return self.task_store.advance_state(
            agent_aic=self.client.partner_aic,
            session_id=session_id,
            task_id=task_id,
            expected=expected,
            new_state=new_state,
        )

    async def _fail_if_active(
        self, key: tuple[str, str], task: str, session: str, code: str, *, uncertain: bool = True
    ) -> None:
        async with self._lock:
            rec = self._tasks.get(key)
            if rec is None or rec.state in (TaskState.Canceled, TaskState.Completed):
                return
            try:
                changed = self._advance(
                    key, ("reserved", "working", "awaiting_completion"),
                    "uncertain" if uncertain else "failed"
                )
            except Exception:
                code = "TASK_STORE_UNAVAILABLE"
            else:
                if not changed:
                    return  # A cancellation or another terminal change won.
            rec.state = TaskState.Failed
        await self.client.fail_task(task, session, code)

    async def on_command(self, command: Any, is_mentioned: bool) -> None:
        if not is_mentioned or getattr(self.client, "is_muted", False):
            return
        if command.senderRole != "leader" or command.senderId != self.leader_aic:
            return
        if not command.taskId or not command.sessionId or command.groupId != self.client.group_id:
            return

        task_id, session_id = command.taskId, command.sessionId
        key = (session_id, task_id)
        action = command.command

        if action == TaskCommandType.Start:
            try:
                v02, legacy = self.parse_business(command)
            except (ValueError, TypeError, KeyError):
                await self.client.reject_task(task_id, session_id, "INVALID_LITERATURE_TASK")
                return

            async with self._lock:
                # Check SQLite before memory so conflicting same-process Starts
                # are rejected, not silently treated as duplicates.
                if self.task_store is not None:
                    try:
                        decision, _previous_state = self.task_store.reserve(
                            agent_aic=self.client.partner_aic,
                            session_id=session_id,
                            task_id=task_id,
                            payload=v02.model_dump(mode="json"),
                        )
                    except Exception:
                        await self.client.reject_task(task_id, session_id, "TASK_STORE_UNAVAILABLE")
                        return
                    if decision == "conflict":
                        await self.client.reject_task(task_id, session_id, "TASK_PAYLOAD_CONFLICT")
                        return
                    if decision == "duplicate":
                        return  # No automatic re-execution or fabricated result.
                if key in self._tasks:
                    return
                self._tasks[key] = _TaskRecord(TaskState.Accepted)

            try:
                await self.client.accept_task(task_id, session_id)
                # Mark intent durably before ANY paid backend invocation.
                if not self._advance(key, ("reserved",), "working"):
                    await self._fail_if_active(key, task_id, session_id, "TASK_STATE_CONFLICT")
                    return
                self._tasks[key].state = TaskState.Working
                await self.client.start_working(task_id, session_id)
                if self._tasks[key].state == TaskState.Canceled:
                    return

                async with asyncio.timeout(self.timeout):
                    raw = await self.executor(legacy)
                data = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
                result_v01 = LegacyResult.model_validate(data)
                result_v02 = result_v01.model_dump(mode="json")
                result_v02["schemaVersion"] = "0.2"
                validate_result(result_v02, v02.model_dump())
                products = [Product(
                    id=f"literature-{task_id}", name="paper-records-v1",
                    dataItems=[StructuredDataItem(data=result_v02)],
                )]

                async with self._lock:
                    if self._tasks[key].state == TaskState.Canceled:
                        return
                    if not self._advance(key, ("working",), "awaiting_completion"):
                        return  # Stale or canceled: do not send a Product.
                    self._tasks[key] = _TaskRecord(TaskState.AwaitingCompletion, products)
                await self.client.submit_for_completion(task_id, session_id, products)

            except asyncio.CancelledError:
                # The remote backend may already have run when we are canceled.
                try:
                    self._advance(
                        key,
                        ("reserved", "working", "awaiting_completion"),
                        "uncertain",
                    )
                except Exception:
                    pass  # The existing durable claim still blocks duplicate Starts.
                raise
            except BusinessExecutionError as exc:
                # Unified execution errors differentiate pre-backend rejection
                # from an execution whose remote side effects are unknown.
                # Never infer "not executed" from an arbitrary/unrecognized
                # error code even when its exception flag is False.
                code = (
                    exc.code if exc.code in SAFE_BUSINESS_ERROR_CODES
                    else "LITERATURE_BACKEND_ERROR"
                )
                uncertain = (
                    exc.remote_execution_uncertain
                    or exc.code not in PRE_EXECUTION_ERROR_CODES
                )
                await self._fail_if_active(
                    key, task_id, session_id, code, uncertain=uncertain,
                )
            except TimeoutError:
                await self._fail_if_active(key, task_id, session_id, "LITERATURE_TIMEOUT")
            except (ValueError, TypeError):
                await self._fail_if_active(
                    key, task_id, session_id, "LITERATURE_INVALID_RESULT_OR_BACKEND",
                    uncertain=False,
                )
            except BackendFailure:
                await self._fail_if_active(
                    key, task_id, session_id, "LITERATURE_INVALID_RESULT_OR_BACKEND"
                )
            except Exception:
                await self._fail_if_active(key, task_id, session_id, "LITERATURE_BACKEND_ERROR")

        elif action == TaskCommandType.Complete:
            async with self._lock:
                rec = self._tasks.get(key)
                if not rec or rec.state != TaskState.AwaitingCompletion:
                    return
                try:
                    # AIP completion must succeed before recording completed.
                    await self.client.complete_task(task_id, session_id)
                except (asyncio.CancelledError, Exception):
                    try:
                        self._advance(key, ("awaiting_completion",), "uncertain")
                    except Exception:
                        pass
                    raise
                # Backend must never restart even when this SQLite write fails.
                if not self._advance(key, ("awaiting_completion",), "completed"):
                    raise RuntimeError("TASK_STATE_CONFLICT")
                rec.state = TaskState.Completed

        elif action == TaskCommandType.Cancel:
            async with self._lock:
                rec = self._tasks.get(key)
                if not rec or rec.state in (TaskState.Completed, TaskState.Canceled, TaskState.Failed):
                    return
                try:
                    await self.client.cancel_task(task_id, session_id)
                except (asyncio.CancelledError, Exception):
                    try:
                        self._advance(
                            key, ("reserved", "working", "awaiting_completion"), "uncertain"
                        )
                    except Exception:
                        pass
                    raise
                if not self._advance(
                    key, ("reserved", "working", "awaiting_completion"), "canceled"
                ):
                    raise RuntimeError("TASK_STATE_CONFLICT")
                rec.state = TaskState.Canceled

        elif action == TaskCommandType.Get:
            rec = self._tasks.get(key)
            if rec:
                await self.client.send_task_result(
                    task_id, session_id, rec.state, products=rec.products
                )
            # Product is not persisted, so restart cannot synthesize it for Get.
