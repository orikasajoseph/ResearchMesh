"""官方 SDK 2.2.0 的 Leader 完成确认组件；不代替 ADP、ACS、证书或 AMP。

需要 Python >=3.10。仅收到产品不算成功；必须校验产品、发 complete、收到 completed。
send 由接入层注入，生产须使用启用身份绑定的官方 AipRpcClient。
"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from acps_sdk.aip import TaskCommand, TaskCommandType, TaskResult, TaskState, StructuredDataItem
from common.models import BusinessTask
from input_binding import validate_task_input
from result_contract import validate_result


class AipExecutionError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def sdk_sender(client):
    """SDK 2.2.0 的 start_task 只接受文本，集中封装其结构化消息底层入口。

不重写 RPC 信封或 TLS。此 protected API 适配点已固定 SDK 版本，升级须重新验收。
"""
    async def send(command):
        response = await client._send_request(command)
        if not isinstance(response.result, TaskResult):
            raise AipExecutionError("AIP_RPC_ERROR")
        return response.result
    return send


class AipTaskRunner:
    def __init__(self, send, leader_aic, partner_aic, *, timeout_seconds=30, poll_seconds=0.2):
        if not leader_aic or not partner_aic or timeout_seconds <= 0 or poll_seconds < 0:
            raise ValueError("需要双方身份和有效超时")
        self.send = send
        self.leader_aic, self.partner_aic = leader_aic, partner_aic
        self.timeout_seconds, self.poll_seconds = timeout_seconds, poll_seconds

    async def run(self, payload, session_id):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id 不能为空")
        BusinessTask.model_validate(payload)
        validate_task_input(payload, dispatch=True)
        task_id = "task-" + str(uuid4())
        events = []
        started = perf_counter()

        async def exchange(command_type, data=None):
            command = TaskCommand(
                id=str(uuid4()), sentAt=datetime.now(timezone.utc).isoformat(),
                senderRole="leader", senderId=self.leader_aic, sessionId=session_id,
                taskId=task_id, command=command_type,
                dataItems=[StructuredDataItem(data=data)] if data is not None else None,
            )
            response = await self.send(command)
            if not isinstance(response, TaskResult):
                raise AipExecutionError("INVALID_AIP_RESULT")
            if (response.taskId != task_id or response.senderId != self.partner_aic
                    or response.senderRole != "partner" or response.sessionId != session_id):
                raise AipExecutionError("AIP_CORRELATION_MISMATCH")
            events.append({"command": command_type.value, "state": response.status.state.value,
                           "at": datetime.now(timezone.utc).isoformat()})
            return response

        def extract(response):
            candidates = [item.data for product in response.products or [] for item in product.dataItems
                          if isinstance(item, StructuredDataItem) and item.data.get("subtaskId") == payload["subtaskId"]]
            if len(candidates) != 1:
                raise AipExecutionError("MISSING_OR_AMBIGUOUS_PRODUCT")
            try:
                validate_result(candidates[0], payload)
                if any(warning.startswith("TRANSPORT_TEST_ONLY") for warning in candidates[0]["warnings"]):
                    raise AipExecutionError("TRANSPORT_ONLY_NOT_RESEARCH")
                if any(item["data"].get("isMock") is True for item in candidates[0]["items"]):
                    raise ValueError("不能确认模拟科研产物")
            except ValueError:
                raise AipExecutionError("INVALID_BUSINESS_RESULT") from None
            return deepcopy(candidates[0])

        async def lifecycle():
            response = await exchange(TaskCommandType.Start, payload)
            candidate, confirmed = None, False
            while True:
                state = response.status.state
                if state in (TaskState.Failed, TaskState.Canceled, TaskState.Rejected):
                    raise AipExecutionError("PARTNER_" + state.value.upper())
                if state == TaskState.AwaitingInput:
                    raise AipExecutionError("PARTNER_NEEDS_INPUT")
                if state == TaskState.Completed:
                    if not confirmed:
                        raise AipExecutionError("COMPLETION_HANDSHAKE_MISSING")
                    if response.products:
                        final = extract(response)
                        if final != candidate:
                            raise AipExecutionError("PRODUCT_CHANGED_AFTER_CONFIRMATION")
                    return candidate
                if state == TaskState.AwaitingCompletion and not confirmed:
                    candidate = extract(response)
                    confirmed = True
                    response = await exchange(TaskCommandType.Complete)
                else:
                    await asyncio.sleep(self.poll_seconds)
                    response = await exchange(TaskCommandType.Get)

        try:
            result = await asyncio.wait_for(lifecycle(), self.timeout_seconds)
        except asyncio.TimeoutError:
            # 不重发 start，不冒充已取消：远程任务可能仍在运行，由接入层处理取消。
            error = AipExecutionError("AIP_TIMEOUT_REMOTE_STATE_UNKNOWN")
            error.trace = {"taskId": task_id, "events": events, "remoteStateUnknown": True}
            raise error from None
        except AipExecutionError as error:
            error.trace = {"taskId": task_id, "events": events}
            raise
        except Exception:
            raise AipExecutionError("AIP_TRANSPORT_ERROR") from None
        return {"result": result, "taskId": task_id, "subtaskId": payload["subtaskId"],
                "agentAic": self.partner_aic, "state": "completed", "events": events,
                "latencyMs": round((perf_counter() - started) * 1000, 3)}
