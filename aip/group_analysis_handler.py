"""Shared analysis Group lifecycle, durable dedup and task-correlated audit.

No RabbitMQ connection in this module; the formal Partner injects the client.
Default backend is not defined here: production must inject a guarded executor.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from acps_sdk.aip import Product, StructuredDataItem, TaskCommandType, TaskState
from business_execution import BusinessExecutionError, validate_business_result
from common.models import BusinessTask, ResearchResult
from input_binding import validate_task_input
from repository_paper_context import prepare_repository_task
from aip.group_task_store import DurableTaskStore
from aip.literature_audit import task_scope
from reader_adapter import to_reader_request

FIELDS = frozenset(BusinessTask.model_fields)
Executor = Callable[[BusinessTask], Awaitable[Any]]
PRE_EXECUTION_ERROR_CODES = frozenset({
    "BUSINESS_EXECUTION_NOT_AUTHORIZED", "INVALID_BUSINESS_CONFIGURATION",
    "INVALID_BUSINESS_TASK", "BUSINESS_SKILL_MISMATCH", "INVALID_BUSINESS_TIMEOUT",
})
SAFE_BUSINESS_ERROR_CODES = PRE_EXECUTION_ERROR_CODES | frozenset({
    "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED", "INVALID_BUSINESS_RESULT",
    "BUSINESS_BACKEND_FAILED", "INVALID_REQUEST", "REPOSITORY_WORKER_FAILED",
    "REPOSITORY_PREVIOUS_EXECUTION_UNCERTAIN", "REPOSITORY_CLONE_FAILED",
    "STATIC_ANALYSIS_FAILED", "REPOSITORY_EMPTY_RESULT", "REPOSITORY_UNAVAILABLE",
    "READER_WORKER_FAILED", "READER_PREVIOUS_EXECUTION_UNCERTAIN", "READER_TIMEOUT",
    "FULL_TEXT_UNAVAILABLE", "PDF_DOWNLOAD_FAILED", "PDF_SOURCE_UNVERIFIED",
    "VERIFIED_PDF_CACHE_UNAVAILABLE",
    "INVALID_REPOSITORY_URL", "BACKEND_UNAVAILABLE",
})


@dataclass
class _TaskRecord:
    state: TaskState
    products: list[Product] | None = None


class AnalysisGroupHandler:
    task_type = "repository_analyze"
    output_schema = "code_records.v1"
    prefix = "REPOSITORY"
    product_name = "code-records-v1"
    minimum_timeout = 180

    def validate_task(self, data):
        if self.task_type == "repository_analyze":
            prepare_repository_task(data)
        else:
            checked = to_reader_request(data)
            if not checked["input"]["papers"][0].get("pdfUrl"):
                raise ValueError("VERIFIED_PDF_URL_REQUIRED")

    def __init__(
        self, client: Any, *, leader_aic: str, executor: Executor,
        task_store: DurableTaskStore, backend_timeout_seconds: float = 210, audit=None,
    ) -> None:
        if not leader_aic or not leader_aic.strip():
            raise ValueError("LEADER_AIC_REQUIRED")
        if task_store is None:
            raise ValueError("DURABLE_TASK_STORE_REQUIRED")
        if (not callable(executor) or type(backend_timeout_seconds) not in (int, float)
                or not math.isfinite(backend_timeout_seconds) or backend_timeout_seconds <= self.minimum_timeout):
            raise ValueError("REPOSITORY_EXECUTOR_OR_TIMEOUT_INVALID")
        self.client = client
        self.leader_aic = leader_aic.strip()
        self.executor = executor
        self.task_store = task_store
        self.timeout = backend_timeout_seconds
        self.audit = audit
        self._lock = asyncio.Lock()
        self._tasks: dict[tuple[str, str], _TaskRecord] = {}

    def parse_business(self, command: Any) -> BusinessTask:
        items = command.dataItems or []
        if len(items) != 1 or not isinstance(items[0], StructuredDataItem):
            raise ValueError("ONE_STRUCTURED_ITEM_REQUIRED")
        payload = items[0].data
        if not isinstance(payload, dict) or set(payload) != FIELDS:
            raise ValueError("EXACT_SEVEN_BUSINESS_FIELDS_REQUIRED")
        task = BusinessTask.model_validate(payload)
        if task.taskType != self.task_type or task.outputSchema != self.output_schema:
            raise ValueError("REPOSITORY_TASK_REQUIRED")
        data = task.model_dump(mode="json")
        validate_task_input(data, dispatch=True)
        self.validate_task(data)
        return task

    def _advance(self, key: tuple[str, str], expected: tuple[str, ...], new_state: str) -> bool:
        session_id, task_id = key
        return self.task_store.advance_state(
            agent_aic=self.client.partner_aic,
            session_id=session_id,
            task_id=task_id,
            expected=expected,
            new_state=new_state,
        )

    async def _fail_if_active(
        self, key: tuple[str, str], task_id: str, session_id: str,
        code: str, *, uncertain: bool = True,
    ) -> None:
        async with self._lock:
            rec = self._tasks.get(key)
            if rec is None or rec.state in (TaskState.Canceled, TaskState.Completed):
                return
            try:
                changed = self._advance(
                    key, ("reserved", "working", "awaiting_completion"),
                    "uncertain" if uncertain else "failed",
                )
            except Exception:
                code = "TASK_STORE_UNAVAILABLE"
            else:
                if not changed:
                    return
            rec.state = TaskState.Failed
        if self.audit:
            self.audit.emit("TASK_FAILED", groupId=self.client.group_id, sessionId=session_id,
                            taskId=task_id, errorCode=code)
        await self.client.fail_task(task_id, session_id, code)

    async def on_command(self, command: Any, is_mentioned: bool) -> None:
        identity = dict(groupId=command.groupId, sessionId=command.sessionId,
                        taskId=command.taskId, agentAic=self.client.partner_aic)
        with task_scope(self.audit, identity):
            try:
                await self._on_command(command, is_mentioned)
            except BaseException as error:
                if self.audit:
                    self.audit.emit("COMMAND_EXCEPTION", **identity,
                                    exceptionCategory="cancelled" if isinstance(error, asyncio.CancelledError) else "command_failed")
                raise

    async def _on_command(self, command: Any, is_mentioned: bool) -> None:
        if not is_mentioned or getattr(self.client, "is_muted", False):
            return
        if command.senderRole != "leader" or command.senderId != self.leader_aic:
            return
        if not command.taskId or not command.sessionId or command.groupId != self.client.group_id:
            return

        task_id, session_id = command.taskId, command.sessionId
        key = (session_id, task_id)
        action = command.command
        identity = dict(groupId=command.groupId, sessionId=session_id,
                        taskId=task_id, agentAic=self.client.partner_aic)
        if self.audit:
            self.audit.emit("TASK_COMMAND", **identity, action=action.value)

        if action == TaskCommandType.Start:
            try:
                task = self.parse_business(command)
            except (ValueError, TypeError, KeyError):
                await self.client.reject_task(task_id, session_id, f"INVALID_{self.prefix}_TASK")
                return

            async with self._lock:
                try:
                    decision, _previous_state = self.task_store.reserve(
                        agent_aic=self.client.partner_aic,
                        session_id=session_id,
                        task_id=task_id,
                        payload=task.model_dump(mode="json"),
                    )
                except Exception:
                    await self.client.reject_task(task_id, session_id, "TASK_STORE_UNAVAILABLE")
                    return
                if self.audit:
                    self.audit.emit("TASK_RESERVATION", **identity, decision=decision, sqliteState=_previous_state)
                if decision == "conflict":
                    await self.client.reject_task(task_id, session_id, "TASK_PAYLOAD_CONFLICT")
                    return
                if decision == "duplicate" or key in self._tasks:
                    return
                self._tasks[key] = _TaskRecord(TaskState.Accepted)

            try:
                await self.client.accept_task(task_id, session_id)
                # Persist work intent BEFORE process spawn, clone, or model use.
                if not self._advance(key, ("reserved",), "working"):
                    await self._fail_if_active(key, task_id, session_id, "TASK_STATE_CONFLICT")
                    return
                self._tasks[key].state = TaskState.Working
                await self.client.start_working(task_id, session_id)
                if self._tasks[key].state == TaskState.Canceled:
                    return

                async with asyncio.timeout(self.timeout):
                    raw = await self.executor(task)
                data = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
                # All six fields and repository evidence must validate BEFORE
                # anything is advertised as a completed Group Product.
                result = ResearchResult.model_validate(data).model_dump(mode="json")
                validate_business_result(task.model_dump(mode="json"), result, repository_analysis_mode="static")
                if self.audit:
                    self.audit.save_result(identity, task.model_dump(mode="json"), result)
                products = [Product(
                    id=f"{self.prefix.lower()}-{task_id}", name=self.product_name,
                    dataItems=[StructuredDataItem(data=result)],
                )]
                async with self._lock:
                    if self._tasks[key].state == TaskState.Canceled:
                        return
                    if not self._advance(key, ("working",), "awaiting_completion"):
                        return
                    self._tasks[key] = _TaskRecord(TaskState.AwaitingCompletion, products)
                await self.client.submit_for_completion(task_id, session_id, products)

            except asyncio.CancelledError:
                try:
                    self._advance(key, ("reserved", "working", "awaiting_completion"), "uncertain")
                except Exception:
                    pass
                raise
            except BusinessExecutionError as exc:
                code = exc.code if exc.code in SAFE_BUSINESS_ERROR_CODES else f"{self.prefix}_BACKEND_ERROR"
                uncertain = exc.remote_execution_uncertain or exc.code not in PRE_EXECUTION_ERROR_CODES
                await self._fail_if_active(key, task_id, session_id, code, uncertain=uncertain)
            except TimeoutError:
                await self._fail_if_active(key, task_id, session_id, f"{self.prefix}_TIMEOUT")
            except (ValueError, TypeError, KeyError):
                # Validation here is after a possible worker run, so mark
                # UNCERTAIN rather than incorrectly labelling it pre-execution.
                await self._fail_if_active(key, task_id, session_id, f"{self.prefix}_INVALID_RESULT")
            except Exception:
                await self._fail_if_active(key, task_id, session_id, f"{self.prefix}_BACKEND_ERROR")

        elif action == TaskCommandType.Complete:
            async with self._lock:
                rec = self._tasks.get(key)
                if not rec or rec.state != TaskState.AwaitingCompletion:
                    return
                try:
                    await self.client.complete_task(task_id, session_id)
                except (asyncio.CancelledError, Exception):
                    try:
                        self._advance(key, ("awaiting_completion",), "uncertain")
                    except Exception:
                        pass
                    raise
                if not self._advance(key, ("awaiting_completion",), "completed"):
                    raise RuntimeError("TASK_STATE_CONFLICT")
                rec.state = TaskState.Completed
                if self.audit:
                    state = self.task_store.lookup(agent_aic=self.client.partner_aic,
                        session_id=session_id, task_id=task_id)
                    self.audit.emit("SQLITE_COMPLETED", **identity, sqliteState=state)

        elif action == TaskCommandType.Cancel:
            async with self._lock:
                rec = self._tasks.get(key)
                if not rec or rec.state in (TaskState.Completed, TaskState.Canceled, TaskState.Failed):
                    return
                try:
                    await self.client.cancel_task(task_id, session_id)
                except (asyncio.CancelledError, Exception):
                    try:
                        self._advance(key, ("reserved", "working", "awaiting_completion"), "uncertain")
                    except Exception:
                        pass
                    raise
                if not self._advance(key, ("reserved", "working", "awaiting_completion"), "canceled"):
                    raise RuntimeError("TASK_STATE_CONFLICT")
                rec.state = TaskState.Canceled

        elif action == TaskCommandType.Get:
            rec = self._tasks.get(key)
            if rec:
                await self.client.send_task_result(task_id, session_id, rec.state, products=rec.products)
            # No persisted Product replay; do not fabricate a response after restart.
