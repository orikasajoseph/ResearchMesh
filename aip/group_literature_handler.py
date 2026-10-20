"""Group-mode TaskCommand -> frozen Literature v0.1 backend -> v0.2 business result.

This module does not create MQ connections; use a fake client for offline tests.
Do not change the frozen wu_delivery wire models.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from acps_sdk.aip import Product, StructuredDataItem, TaskCommandType, TaskState
from aip.literature_bridge import build_literature_request, execute_literature
from aip.wu_delivery.contracts import ResearchResult as LegacyResult
from aip.wu_delivery.contracts import ResearchTask as LegacyTask
from aip.wu_delivery.execution import BackendFailure
from common.models import BusinessTask
from input_binding import validate_task_input
from result_contract import validate_result

FIELDS = frozenset(BusinessTask.model_fields)
Executor = Callable[[LegacyTask], Awaitable[Any]]


@dataclass
class _TaskRecord:
    state: TaskState
    products: list[Product] | None = None


class LiteratureGroupHandler:
    def __init__(
        self, client: Any, *, leader_aic: str,
        executor: Executor = execute_literature,
        backend_timeout_seconds: float = 120.0,
    ) -> None:
        if not leader_aic.strip() or backend_timeout_seconds <= 0:
            raise ValueError("LEADER_AIC_AND_TIMEOUT_REQUIRED")
        self.client = client
        self.leader_aic = leader_aic.strip()
        self.executor = executor
        self.timeout = backend_timeout_seconds
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
        build_literature_request(legacy)  # Validate Literature-specific limits.
        return v02, legacy

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
                # Repeated starts must never re-run a potentially paid search.
                if key in self._tasks:
                    return
                self._tasks[key] = _TaskRecord(TaskState.Accepted)
            try:
                await self.client.accept_task(task_id, session_id)
                self._tasks[key].state = TaskState.Working
                await self.client.start_working(task_id, session_id)
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
                if self._tasks[key].state == TaskState.Canceled:
                    return
                self._tasks[key] = _TaskRecord(TaskState.AwaitingCompletion, products)
                await self.client.submit_for_completion(task_id, session_id, products)
            except TimeoutError:
                await self._fail_if_active(key, task_id, session_id, "LITERATURE_TIMEOUT")
            except (ValueError, TypeError, BackendFailure):
                await self._fail_if_active(key, task_id, session_id, "LITERATURE_INVALID_RESULT_OR_BACKEND")
            except Exception:
                # Never log or send upstream error text (may include credentials).
                await self._fail_if_active(key, task_id, session_id, "LITERATURE_BACKEND_ERROR")

        elif action == TaskCommandType.Complete:
            rec = self._tasks.get(key)
            if rec and rec.state == TaskState.AwaitingCompletion:
                await self.client.complete_task(task_id, session_id)
                rec.state = TaskState.Completed
        elif action == TaskCommandType.Cancel:
            rec = self._tasks.get(key)
            if rec and rec.state not in (TaskState.Completed, TaskState.Canceled, TaskState.Failed):
                rec.state = TaskState.Canceled
                await self.client.cancel_task(task_id, session_id)
        elif action == TaskCommandType.Get:
            rec = self._tasks.get(key)
            if rec:
                await self.client.send_task_result(task_id, session_id, rec.state, products=rec.products)

    async def _fail_if_active(self, key: tuple[str, str], task: str, session: str, code: str) -> None:
        rec = self._tasks[key]
        if rec.state in (TaskState.Canceled, TaskState.Completed):
            return
        rec.state = TaskState.Failed
        await self.client.fail_task(task, session, code)
