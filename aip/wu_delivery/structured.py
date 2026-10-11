"""Small SDK compatibility adapter for structured TaskCommand inputs."""

import uuid
from datetime import datetime, timezone

from acps_sdk.aip import AipRpcClient, StructuredDataItem, TaskCommand, TaskResult
from acps_sdk.aip.aip_base_model import TaskCommandType

from .contracts import ResearchTask


class ResearchClient(AipRpcClient):
    async def start_research(self, task: ResearchTask, *, session_id: str, task_id: str):
        command = TaskCommand(
            id=str(uuid.uuid4()), sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader", senderId=self.leader_id,
            sessionId=session_id, taskId=task_id, command=TaskCommandType.Start,
            dataItems=[StructuredDataItem(data=task.model_dump())],
        )
        # SDK 2.2.0 exposes text-only start_task; isolate its protected sender here.
        response = await self._send_request(command)
        if not isinstance(response.result, TaskResult):
            raise TypeError("Expected official TaskResult")
        return response.result
