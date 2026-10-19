"""Local v0.2 Partner seam following Wu's SDK handler design; no provider imports.

The exported app is FIXTURE ONLY. It must only be bound to loopback. Identity
binding is disabled for this test, so it is not a platform/public deployment.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from uuid import uuid4

from fastapi import FastAPI
from acps_sdk.aip import Product, TaskResult, TaskState, TaskStatus, StructuredDataItem, TextDataItem
from acps_sdk.aip.aip_rpc_server import CommandHandlers, DefaultHandlers, TaskManager, add_aip_rpc_router
from common.models import BusinessTask
from common.skills import OUTPUT_SCHEMAS
from input_binding import paper_ref, validate_task_input
from planner_contract import ALLOWED_CONSTRAINTS
from result_contract import validate_result


class LocalBackendFailure(RuntimeError):
    """Only allowlisted codes are sent to the peer; never raw provider exceptions."""
    def __init__(self, code):
        allowed = {"BACKEND_UNAVAILABLE", "FULL_TEXT_UNAVAILABLE", "RATE_LIMITED", "NO_RESULTS",
                   "INVALID_REQUEST", "PROVIDER_UNAVAILABLE", "UNKNOWN_PROVIDER_ERROR",
                   "NO_VALID_PAPER_RECORDS", "WRAPPER_TIMEOUT", "WRAPPER_CALL_FAILED",
                   "READER_TIMEOUT", "READER_CALL_FAILED", "INVALID_READER_RESULT", "READER_EMPTY_RESULT",
                   "READER_WORKER_FAILED", "READER_PREVIOUS_EXECUTION_UNCERTAIN",
                   "PDF_DOWNLOAD_FAILED", "PDF_SOURCE_UNVERIFIED",
                   "VERIFIED_PDF_CACHE_UNAVAILABLE", "INVALID_BOUND_TASK_PATH",
                   "REPOSITORY_UNAVAILABLE", "INVALID_REPOSITORY_URL",
                   "REPOSITORY_CLONE_FAILED", "STATIC_ANALYSIS_FAILED"}
        self.code = code if code in allowed else "BACKEND_FAILED"
        super().__init__(self.code)


def validate_local_task(payload):
    task = BusinessTask.model_validate(payload).model_dump()
    if task["taskType"] not in ("literature_search", "paper_read", "repository_analyze"):
        raise ValueError("UNSUPPORTED_TASK")
    if task["outputSchema"] != OUTPUT_SCHEMAS[task["taskType"]]:
        raise ValueError("OUTPUT_SCHEMA_MISMATCH")
    if set(task["constraints"]) - ALLOWED_CONSTRAINTS[task["taskType"]]:
        raise ValueError("UNSUPPORTED_CONSTRAINT")
    if task["taskType"] == "repository_analyze" and task["input"].get("repositoryUrls") == []:
        raise ValueError("REPOSITORY_UNAVAILABLE")
    validate_task_input(task, dispatch=True)
    if task["taskType"] == "repository_analyze":
        # Reject malformed paper_notes.v1 before the AIP task can start its backend.
        from aip_repository_partner import repository_request
        repository_request(task)
    if task["taskType"] == "paper_read":
        if task["constraints"].get("maxPapers") != 1 or len(task["input"]["papers"]) != 1:
            raise ValueError("SINGLE_PAPER_ONLY")
    return task


def create_local_business_app(*, execute, partner_aic, backend_timeout_seconds=10,
                              background_execution=False, mode="local-business-fixture"):
    """Inject an async executor; fixture default stays synchronous.

    Optional background mode returns working immediately and is queried via get,
    avoiding the SDK's 30-second single HTTP timeout. Still in-memory, local only,
    not a durable long-running Reader/platform service.
    """
    if (not callable(execute) or not isinstance(partner_aic, str) or not partner_aic.strip()
            or isinstance(backend_timeout_seconds, bool)
            or not isinstance(backend_timeout_seconds, (float, int))
            or not math.isfinite(backend_timeout_seconds) or backend_timeout_seconds <= 0
            or type(background_execution) is not bool
            or mode not in {"local-business-fixture", "local-literature-live", "local-reader-live", "local-repository-live"}):
        raise ValueError("INVALID_LOCAL_CONFIGURATION")
    owners, requests, jobs = {}, {}, {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            pending = list(jobs.values())
            for job in pending:
                job.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    app = FastAPI(title="ResearchMesh v0.2 local AIP business seam", lifespan=lifespan)

    def rejected(command, code):
        now = datetime.now(timezone.utc).isoformat()
        return TaskResult(id=str(uuid4()), sentAt=now, senderRole="partner", senderId=partner_aic,
                          taskId=command.taskId, sessionId=command.sessionId,
                          status=TaskStatus(state=TaskState.Rejected, stateChangedAt=now,
                                            dataItems=[TextDataItem(text=code)]))

    def owns(command, existing):
        return (existing is not None and existing.senderId == partner_aic
                and owners.get(command.taskId) == (command.senderId, command.sessionId)
                and command.senderRole == "leader")

    async def start(command, existing):
        if command.senderRole != "leader" or not command.senderId or not command.sessionId or not command.taskId:
            return rejected(command, "LEADER_ROLE_REQUIRED")
        structured = [item for item in command.dataItems or [] if isinstance(item, StructuredDataItem)]
        try:
            if len(structured) != 1 or len(command.dataItems or []) != 1:
                raise ValueError("ONE_STRUCTURED_TASK_REQUIRED")
            payload = validate_local_task(structured[0].data)
        except (ValueError, TypeError, KeyError) as error:
            return rejected(command, "REPOSITORY_UNAVAILABLE" if str(error) == "REPOSITORY_UNAVAILABLE"
                            else "INVALID_BUSINESS_TASK")
        if mode == "local-literature-live" and payload["taskType"] != "literature_search":
            return rejected(command, "ONLY_LITERATURE_LIVE_SUPPORTED")
        if mode == "local-reader-live" and payload["taskType"] != "paper_read":
            return rejected(command, "ONLY_READER_LIVE_SUPPORTED")
        if mode == "local-repository-live" and payload["taskType"] != "repository_analyze":
            return rejected(command, "ONLY_REPOSITORY_LIVE_SUPPORTED")
        if existing is not None:
            if not owns(command, existing) or requests.get(command.taskId) != payload:
                return rejected(command, "TASK_OWNERSHIP_OR_PAYLOAD_MISMATCH")
            return existing
        task = TaskManager.create_task(command, initial_state=TaskState.Working)
        task.senderId = partner_aic
        owners[task.taskId] = (command.senderId, command.sessionId)
        requests[task.taskId] = deepcopy(payload)

        if background_execution:
            job = asyncio.create_task(finish(task, payload))
            jobs[task.taskId] = job
            def done(finished):
                jobs.pop(task.taskId, None)
                if not finished.cancelled():
                    finished.exception()  # consume errors; never print raw messages
            job.add_done_callback(done)
            return task
        return await finish(task, payload)

    async def finish(task, payload):
        try:
            result = await asyncio.wait_for(execute(deepcopy(payload)), backend_timeout_seconds)
            validate_result(result, payload)
            if payload["taskType"] == "paper_read":
                if (len(result["items"]) != 1
                        or paper_ref(result["items"][0]["data"]["source"]) !=
                        paper_ref(payload["input"]["papers"][0])):
                    raise ValueError("READER_RESULT_SOURCE_MISMATCH")
            elif payload["taskType"] == "literature_search" and len(result["items"]) > payload["constraints"].get("maxResults", 100):
                raise ValueError("SEARCH_RESULT_LIMIT_EXCEEDED")
        except asyncio.TimeoutError:
            code = "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED"
        except LocalBackendFailure as error:
            code = error.code
        except asyncio.CancelledError:
            if task.status.state == TaskState.Working:
                TaskManager.update_task_status(task.taskId, TaskState.Canceled,
                                              [TextDataItem(text="LOCAL_HANDLER_CANCELED")])
            raise
        except Exception:
            code = "BACKEND_OR_RESULT_INVALID"
        else:
            # A separate cancel RPC may have arrived while execute awaited.
            if task.status.state == TaskState.Working:
                TaskManager.set_products(task.taskId, [Product(
                    id=f"research-result-{task.taskId}", name="researchmesh-result-v02",
                    dataItems=[StructuredDataItem(data=deepcopy(result))])])
                TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
            return task
        if task.status.state == TaskState.Working:
            TaskManager.update_task_status(task.taskId, TaskState.Failed,
                                          [TextDataItem(text=code)])
        return task

    def scoped(handler):
        async def wrapped(command, existing):
            if not owns(command, existing):
                return rejected(command, "TASK_OWNERSHIP_MISMATCH")
            return await handler(command, existing)
        return wrapped

    async def continue_unsupported(command, existing):
        return rejected(command, "CONTINUE_NOT_SUPPORTED_LOCAL")

    async def cancel(command, existing):
        result = await DefaultHandlers.cancel(command, existing)
        job = jobs.get(existing.taskId)
        if job is not None and result.status.state == TaskState.Canceled:
            job.cancel()  # best-effort local cancellation, not a provider rollback
        return result

    add_aip_rpc_router(app, "/rpc", CommandHandlers(
        on_start=start, on_get=scoped(DefaultHandlers.get),
        on_complete=scoped(DefaultHandlers.complete), on_cancel=scoped(cancel),
        on_continue=scoped(continue_unsupported)), local_aic=partner_aic,
        identity_binding_enabled=False)

    @app.get("/health")
    async def health():
        return {"status": "ok", "aic": partner_aic, "mode": mode,
                "schemaVersion": "0.2", "skills": ["literature.search"] if mode == "local-literature-live"
                else ["paper.analyze"] if mode == "local-reader-live"
                else ["repository.analyze"] if mode == "local-repository-live"
                else ["literature.search", "paper.analyze", "repository.analyze"],
                "executionEvidence": "see task records, not health", "identityVerified": False,
                "backgroundExecution": background_execution}
    return app


async def fixture_execute(task):
    """Synthetic records, not Zhang's real wrappers or saved live-result replay."""
    if task["taskType"] == "literature_search":
        items = [{"kind": "paper", "data": {
            "paperId": "mock-aip-paper-001", "title": "【模拟】AIP 业务联调论文",
            "doi": None, "url": "https://example.invalid/aip/paper-001",
            "pdfUrl": "https://example.invalid/aip/paper-001.pdf", "repositoryUrls": [],
            "isMock": True}}]
    else:
        source = deepcopy(task["input"]["papers"][0])
        items = [{"kind": "paper_note", "data": {
            "source": source, "method": "【模拟】仅验证阅读产物结构，未阅读全文。",
            "findings": "【模拟】验证检索产物经 Leader 绑定后可传给 Reader Partner。",
            "limitations": "【模拟】无科研依据，不能作为论文分析。", "isMock": True,
            "evidence": [{"sourceId": source["paperId"], "url": source["pdfUrl"] or source["url"],
                          "locator": "【模拟】无真实页码", "supports": "仅支持接口联调，不支持科研结论。"}]}}]
    return {"schemaVersion": "0.2", "subtaskId": task["subtaskId"], "taskType": task["taskType"],
            "summary": "【模拟科研结果】通过本地 AIP 返回业务产物；未调用模型或论文 API。",
            "items": items, "warnings": ["SIMULATED_RESEARCH: fixture only; not research evidence"]}


app = create_local_business_app(execute=fixture_execute,
                               partner_aic=os.environ.get("RESEARCHMESH_LOCAL_PARTNER_AIC", "local-fixture-partner"))
