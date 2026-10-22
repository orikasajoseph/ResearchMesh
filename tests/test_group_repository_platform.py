"""Offline fake-client tests for Repository static Group + durable idempotency.

No AMQP, Git clone, CodeWiki subprocess, DeepSeek or HTTP request is made.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from acps_sdk.aip import StructuredDataItem, TaskCommand, TaskCommandType
from aip.group_task_store import DurableTaskStore
from aip.group_repository_handler import RepositoryGroupHandler
from business_execution import BusinessExecutionError

LEADER = "test-leader-aic"
PARTNER = "test-repository-aic"
SESSION = "test-session"
TASK_ID = "repo-task-1"
REPO = "https://github.com/orikasajoseph/ResearchMesh"


def task_payload(subtask_id="R1"):
    return {
        "schemaVersion": "0.2",
        "subtaskId": subtask_id,
        "taskType": "repository_analyze",
        "goal": "Inspect explicit Repository static evidence",
        "input": {"repositoryUrls": [REPO]},
        "constraints": {
            "maxRepositories": 1, "executeCode": False,
            "allowRepositorySearch": False,
        },
        "outputSchema": "code_records.v1",
    }


def valid_result(payload):
    return {
        "schemaVersion": "0.2",
        "subtaskId": payload.subtaskId,
        "taskType": "repository_analyze",
        "summary": "Offline static evidence fixture (not a real research result)",
        "items": [{"kind": "repository", "data": {
            "url": REPO,
            "license": "UNKNOWN",
            "relationEvidence": "No paper/repository association verified",
            "evidence": [{
                "sourceId": "test-static-1",
                "url": REPO + "/blob/" + "a" * 40 + "/README.md#L1",
                "locator": "README.md:L1",
                "supports": "Locally supplied test evidence",
                "text": "fake static repository evidence",
            }],
            "isMock": False,
        }}],
        "warnings": ["OFFLINE_TEST_ONLY"],
    }


def command(action=TaskCommandType.Start, *, payload=None, sender=LEADER, task_id=TASK_ID):
    return TaskCommand(
        id="command-1",
        sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="leader", senderId=sender,
        groupId="test-group", sessionId=SESSION, taskId=task_id,
        command=action, mentions=[PARTNER],
        dataItems=[StructuredDataItem(data=payload if payload is not None else task_payload())],
    )


class Client:
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


class RepositoryGroupTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "durable.sqlite3"
        self.store = DurableTaskStore(self.db)
        self.client = Client()
        self.executor = AsyncMock(side_effect=valid_result)
        self.handler = RepositoryGroupHandler(
            self.client, leader_aic=LEADER, executor=self.executor,
            task_store=self.store,
        )

    def state(self, task_id=TASK_ID):
        return self.store.lookup(
            agent_aic=PARTNER, session_id=SESSION, task_id=task_id,
        )

    async def test_start_then_complete(self):
        await self.handler.on_command(command(), True)
        self.executor.assert_awaited_once()
        self.client.submit_for_completion.assert_awaited_once()
        products = self.client.submit_for_completion.await_args.args[2]
        item = products[0].dataItems[0].data
        self.assertEqual(set(item), {
            "schemaVersion", "subtaskId", "taskType", "summary", "items", "warnings",
        })
        self.assertEqual(item["items"][0]["kind"], "repository")
        self.assertEqual(self.state(), "awaiting_completion")
        await self.handler.on_command(command(TaskCommandType.Complete), True)
        self.assertEqual(self.state(), "completed")
        self.client.complete_task.assert_awaited_once()

    async def test_same_task_duplicate_across_restart_never_reexecutes(self):
        await self.handler.on_command(command(), True)
        restarted_client = Client()
        restarted = RepositoryGroupHandler(
            restarted_client, leader_aic=LEADER, executor=self.executor,
            task_store=DurableTaskStore(self.db),
        )
        await restarted.on_command(command(), True)
        self.executor.assert_awaited_once()
        restarted_client.accept_task.assert_not_awaited()

    async def test_conflicting_task_payload_rejected(self):
        await self.handler.on_command(command(), True)
        changed = task_payload()
        changed["goal"] = "Different analysis request"
        await self.handler.on_command(command(payload=changed), True)
        self.client.reject_task.assert_awaited_once_with(
            TASK_ID, SESSION, "TASK_PAYLOAD_CONFLICT",
        )
        self.executor.assert_awaited_once()

    async def test_invalid_repository_input_rejected_before_worker(self):
        bad = task_payload()
        bad["constraints"]["executeCode"] = True
        await self.handler.on_command(command(payload=bad), True)
        self.client.reject_task.assert_awaited_once_with(
            TASK_ID, SESSION, "INVALID_REPOSITORY_TASK",
        )
        self.executor.assert_not_awaited()
        self.assertIsNone(self.state())

    async def test_wrong_leader_rejected_before_worker(self):
        await self.handler.on_command(command(sender="other-aic"), True)
        self.executor.assert_not_awaited()
        self.client.reject_task.assert_not_awaited()
        self.assertIsNone(self.state())

    async def test_deterministic_pre_execution_failure_is_failed(self):
        self.executor.side_effect = BusinessExecutionError("INVALID_BUSINESS_TASK")
        await self.handler.on_command(command(), True)
        self.assertEqual(self.state(), "failed")
        self.client.fail_task.assert_awaited_once_with(
            TASK_ID, SESSION, "INVALID_BUSINESS_TASK",
        )

    async def test_timeout_is_uncertain_and_never_retried(self):
        self.executor.side_effect = BusinessExecutionError(
            "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED",
            remote_execution_uncertain=True,
        )
        await self.handler.on_command(command(), True)
        self.assertEqual(self.state(), "uncertain")
        self.client.fail_task.assert_awaited_once_with(
            TASK_ID, SESSION, "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED",
        )
        await self.handler.on_command(command(), True)
        self.executor.assert_awaited_once()

    async def test_invalid_result_is_uncertain(self):
        bad = valid_result(type("Dummy", (), {"subtaskId": "R1"})())
        bad["items"][0]["data"]["evidence"] = []
        self.executor.side_effect = None
        self.executor.return_value = bad
        await self.handler.on_command(command(), True)
        self.assertEqual(self.state(), "uncertain")
        self.client.submit_for_completion.assert_not_awaited()
        self.client.fail_task.assert_awaited_once_with(
            TASK_ID, SESSION, "REPOSITORY_INVALID_RESULT",
        )

    async def test_independent_tasks_use_same_group_with_separate_durable_claims(self):
        await self.handler.on_command(command(), True)
        await self.handler.on_command(
            command(task_id="repo-task-2", payload=task_payload("R2")), True,
        )
        self.assertEqual(self.executor.await_count, 2)
        self.assertEqual(self.state("repo-task-2"), "awaiting_completion")
        self.assertEqual(self.state(), "awaiting_completion")

    async def test_corrupt_store_rejects_without_backend(self):
        self.store.reserve = lambda **kwargs: (_ for _ in ()).throw(OSError("simulate DB failure"))
        await self.handler.on_command(command(), True)
        self.executor.assert_not_awaited()
        self.client.reject_task.assert_awaited_once_with(
            TASK_ID, SESSION, "TASK_STORE_UNAVAILABLE",
        )
