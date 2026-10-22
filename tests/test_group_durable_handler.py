"""Offline checks for durable Literature Group task lifecycle.

No MQ, external HTTP or paid provider calls.
"""
import asyncio
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from acps_sdk.aip import StructuredDataItem, TaskCommand, TaskCommandType
from aip.group_literature_handler import LiteratureGroupHandler
from aip.group_task_store import DurableTaskStore

LEADER = "test-leader-aic"
PARTNER = "test-literature-aic"
SESSION = "session-1"
TASK = "task-1"


def task_payload():
    return {
        "schemaVersion": "0.2",
        "subtaskId": "S1",
        "taskType": "literature_search",
        "goal": "Find LoRA paper",
        "input": {"query": "LoRA: Low-Rank Adaptation of Large Language Models"},
        "constraints": {"maxResults": 1},
        "outputSchema": "paper_records.v1",
    }


def command(action=TaskCommandType.Start, payload=None):
    return TaskCommand(
        id="command-1",
        sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="leader",
        senderId=LEADER,
        groupId="test-group",
        sessionId=SESSION,
        taskId=TASK,
        command=action,
        mentions=[PARTNER],
        dataItems=[StructuredDataItem(data=payload if payload is not None else task_payload())],
    )


async def fake_backend(request):
    return {
        "schemaVersion": "0.1",
        "subtaskId": request.subtaskId,
        "taskType": "literature_search",
        "summary": "One paper",
        "warnings": [],
        "items": [{"kind": "paper", "data": {
            "paperId": "paper-1",
            "title": "LoRA: Low-Rank Adaptation of Large Language Models",
            "doi": "10.1000/example",
            "url": "https://doi.org/10.1000/example",
            "pdfUrl": None,
            "repositoryUrls": [],
        }}],
    }


class FakeClient:
    partner_aic = PARTNER
    group_id = "test-group"
    is_muted = False

    def __init__(self):
        for name in (
            "accept_task", "start_working", "submit_for_completion",
            "complete_task", "reject_task", "fail_task", "cancel_task",
            "send_task_result",
        ):
            setattr(self, name, AsyncMock())


class DurableHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "tasks.sqlite3"
        self.store = DurableTaskStore(self.db_path)
        self.client = FakeClient()
        self.backend = AsyncMock(side_effect=fake_backend)
        self.handler = LiteratureGroupHandler(
            self.client, leader_aic=LEADER,
            executor=self.backend, task_store=self.store,
        )

    def restarted_handler(self):
        client = FakeClient()
        handler = LiteratureGroupHandler(
            client, leader_aic=LEADER, executor=self.backend,
            task_store=DurableTaskStore(self.db_path),
        )
        return handler, client

    def persisted_state(self):
        return self.store.lookup(
            agent_aic=PARTNER, session_id=SESSION, task_id=TASK
        )

    async def test_start_creates_durable_claim(self):
        await self.handler.on_command(command(), True)
        self.backend.assert_awaited_once()
        self.client.submit_for_completion.assert_awaited_once()
        self.assertEqual(self.persisted_state(), "awaiting_completion")

    async def test_restart_blocks_duplicate_backend(self):
        await self.handler.on_command(command(), True)
        restarted, new_client = self.restarted_handler()
        await restarted.on_command(command(), True)
        self.assertEqual(self.backend.await_count, 1)
        new_client.accept_task.assert_not_awaited()
        new_client.submit_for_completion.assert_not_awaited()
        self.assertEqual(self.persisted_state(), "awaiting_completion")

    async def test_restart_rejects_payload_conflict(self):
        await self.handler.on_command(command(), True)
        restarted, new_client = self.restarted_handler()
        changed = task_payload()
        changed["goal"] = "Different research goal"
        await restarted.on_command(command(payload=changed), True)
        self.backend.assert_awaited_once()
        new_client.reject_task.assert_awaited_once_with(
            TASK, SESSION, "TASK_PAYLOAD_CONFLICT"
        )

    async def test_same_process_rejects_payload_conflict(self):
        await self.handler.on_command(command(), True)
        changed = task_payload()
        changed["goal"] = "Different research goal"
        await self.handler.on_command(command(payload=changed), True)
        self.backend.assert_awaited_once()
        self.client.reject_task.assert_awaited_once_with(
            TASK, SESSION, "TASK_PAYLOAD_CONFLICT"
        )

    async def test_store_failure_blocks_execution(self):
        with patch.object(
            self.store, "reserve",
            side_effect=sqlite3.OperationalError("simulated database failure"),
        ):
            await self.handler.on_command(command(), True)
        self.backend.assert_not_awaited()
        self.client.reject_task.assert_awaited_once_with(
            TASK, SESSION, "TASK_STORE_UNAVAILABLE"
        )
        self.assertIsNone(self.persisted_state())

    async def test_working_state_write_error_blocks_backend(self):
        with patch.object(
            self.store, "advance_state",
            side_effect=sqlite3.OperationalError("test failed write"),
        ):
            await self.handler.on_command(command(), True)
        self.backend.assert_not_awaited()
        self.client.fail_task.assert_awaited_once_with(
            TASK, SESSION, "TASK_STORE_UNAVAILABLE"
        )
        self.assertEqual(self.persisted_state(), "reserved")

    async def test_complete_persisted(self):
        await self.handler.on_command(command(), True)
        await self.handler.on_command(command(TaskCommandType.Complete), True)
        self.client.complete_task.assert_awaited_once_with(TASK, SESSION)
        self.assertEqual(self.persisted_state(), "completed")
        restarted, new_client = self.restarted_handler()
        await restarted.on_command(command(), True)
        new_client.accept_task.assert_not_awaited()
        self.backend.assert_awaited_once()

    async def test_cancel_persisted(self):
        await self.handler.on_command(command(), True)
        await self.handler.on_command(command(TaskCommandType.Cancel), True)
        self.client.cancel_task.assert_awaited_once_with(TASK, SESSION)
        self.assertEqual(self.persisted_state(), "canceled")
        restarted, _ = self.restarted_handler()
        await restarted.on_command(command(), True)
        self.backend.assert_awaited_once()

    async def test_backend_exception_persists_uncertain(self):
        self.backend.side_effect = RuntimeError("fake provider failure")
        await self.handler.on_command(command(), True)
        self.client.fail_task.assert_awaited_once_with(
            TASK, SESSION, "LITERATURE_BACKEND_ERROR"
        )
        self.assertEqual(self.persisted_state(), "uncertain")
        restarted, _ = self.restarted_handler()
        await restarted.on_command(command(), True)
        self.assertEqual(self.backend.await_count, 1)

    async def test_invalid_result_persists_failed(self):
        self.backend.side_effect = ValueError("invalid normalized result")
        await self.handler.on_command(command(), True)
        self.client.fail_task.assert_awaited_once_with(
            TASK, SESSION, "LITERATURE_INVALID_RESULT_OR_BACKEND"
        )
        self.assertEqual(self.persisted_state(), "failed")

    async def test_timeout_persists_uncertain(self):
        async def slow(_task):
            await asyncio.sleep(0.1)
        self.backend.side_effect = slow
        self.handler.timeout = 0.001
        await self.handler.on_command(command(), True)
        self.client.fail_task.assert_awaited_once_with(
            TASK, SESSION, "LITERATURE_TIMEOUT"
        )
        self.assertEqual(self.persisted_state(), "uncertain")


if __name__ == "__main__":
    unittest.main()
