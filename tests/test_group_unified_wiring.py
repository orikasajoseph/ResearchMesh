"""Offline Group Partner -> SQLite -> unified executor -> AIP Result test.

Mocks AMQP client and Literature HTTP pipeline; no real network or paid calls.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from acps_sdk.aip import StructuredDataItem, TaskCommand, TaskCommandType

from aip.group_literature_partner import serve
from aip.group_task_store import DurableTaskStore


LEADER = "test-leader-aic"
PARTNER = "test-literature-aic"
GROUP = "rm-offline-group"
SESSION = "session-offline-1"
TASK = "task-offline-1"


def business_task() -> dict:
    return {
        "schemaVersion": "0.2",
        "subtaskId": "S1",
        "taskType": "literature_search",
        "goal": "Find the LoRA paper",
        "input": {"query": "LoRA: Low-Rank Adaptation of Large Language Models"},
        "constraints": {"maxResults": 1},
        "outputSchema": "paper_records.v1",
    }


def business_result() -> dict:
    return {
        "schemaVersion": "0.2",
        "subtaskId": "S1",
        "taskType": "literature_search",
        "summary": "One paper found",
        "items": [{
            "kind": "paper",
            "data": {
                "paperId": "test-lora",
                "title": "LoRA: Low-Rank Adaptation of Large Language Models",
                "doi": None,
                "url": "https://arxiv.org/abs/2106.09685",
                "pdfUrl": "https://arxiv.org/pdf/2106.09685",
                "repositoryUrls": [],
            },
        }],
        "warnings": [],
    }


def command(action: TaskCommandType = TaskCommandType.Start) -> TaskCommand:
    return TaskCommand(
        id="command-offline-1",
        sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="leader",
        senderId=LEADER,
        groupId=GROUP,
        sessionId=SESSION,
        taskId=TASK,
        command=action,
        mentions=[PARTNER],
        dataItems=[StructuredDataItem(data=business_task())],
    )


class FakePartner:
    def __init__(self, ready: asyncio.Event):
        self.ready = ready
        self.partner_aic = PARTNER
        self.group_id = GROUP
        self.is_muted = False
        self.command_handler = None
        self.closed = False
        self.leave_finished = asyncio.Event()
        self.leave_succeeded = False
        for method in (
            "accept_task", "start_working", "submit_for_completion",
            "complete_task", "reject_task", "fail_task", "cancel_task",
            "send_task_result",
        ):
            setattr(self, method, AsyncMock())

    def set_command_handler(self, handler):
        self.command_handler = handler

    def set_mgmt_command_handler(self, handler):
        self.mgmt_handler = handler

    def set_disconnect_handler(self, handler):
        self.disconnect_handler = handler

    def set_invitation_handler(self, handler):
        self.invitation_handler = handler

    async def connect(self):
        pass

    async def start_inbox_consuming(self, callback):
        self.inbox_callback = callback
        self.ready.set()

    async def close(self):
        self.closed = True


class UnifiedGroupWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_task_invokes_unified_pipeline_once_and_completes(self):
        with TemporaryDirectory() as tmp:
            store = DurableTaskStore(Path(tmp) / "group_tasks.sqlite3")
            ready = asyncio.Event()
            fake = FakePartner(ready)
            with (
                patch(
                    "aip.group_literature_partner.GroupPartnerMqClient",
                    return_value=fake,
                ),
                patch(
                    "aip.group_literature_partner.tls_context",
                    return_value=object(),
                ),
                patch(
                    "aip.literature_unified_executor.execute_live_task",
                    new_callable=AsyncMock,
                    return_value={"error": None, "result": business_result()},
                ) as pipeline,
            ):
                server = asyncio.create_task(serve(
                    partner_aic=PARTNER,
                    leader_aic=LEADER,
                    credential_dir=Path(tmp),
                    task_store=store,
                ))
                try:
                    await asyncio.wait_for(ready.wait(), 2)
                    self.assertIsNotNone(fake.command_handler)

                    await fake.command_handler(command(), True)
                    pipeline.assert_awaited_once()
                    self.assertFalse(pipeline.await_args.kwargs["retry"])
                    self.assertEqual(
                        store.lookup(agent_aic=PARTNER, session_id=SESSION, task_id=TASK),
                        "awaiting_completion",
                    )
                    fake.submit_for_completion.assert_awaited_once()
                    products = fake.submit_for_completion.await_args.args[2]
                    result = products[0].dataItems[0].data
                    self.assertEqual(len(business_task()), 7)
                    self.assertEqual(len(result), 6)
                    self.assertEqual(result["schemaVersion"], "0.2")
                    self.assertEqual(len(result["items"]), 1)

                    # Repeated Start must never trigger another backend call.
                    await fake.command_handler(command(), True)
                    pipeline.assert_awaited_once()

                    await fake.command_handler(
                        command(TaskCommandType.Complete), True
                    )
                    fake.complete_task.assert_awaited_once()
                    self.assertEqual(
                        store.lookup(agent_aic=PARTNER, session_id=SESSION, task_id=TASK),
                        "completed",
                    )
                finally:
                    server.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await server

            self.assertTrue(fake.closed)
            print("GROUP_UNIFIED_WIRING_OK: True")
            print("BACKEND_CALLS: 1")
            print("DURABLE_COMPLETED: True")
            print("OFFLINE_ONLY: True")

    async def test_missing_store_prevents_connection(self):
        with self.assertRaises(RuntimeError) as caught:
            await serve(
                partner_aic=PARTNER,
                leader_aic=LEADER,
                credential_dir=Path("."),
                task_store=None,
            )
        self.assertEqual(str(caught.exception), "DURABLE_TASK_STORE_REQUIRED")


if __name__ == "__main__":
    unittest.main()
