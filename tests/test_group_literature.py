"""Offline tests only. No external calls, MQ connection or real papers."""
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from acps_sdk.aip import StructuredDataItem, TaskCommand, TaskCommandType, TaskState
from aip.group_literature_handler import LiteratureGroupHandler
from aip.group_literature_partner import invitation_allowed

LEADER = "official-leader-aic"


def task_payload():
    return {
        "schemaVersion": "0.2", "subtaskId": "lit-1",
        "taskType": "literature_search", "goal": "Find research",
        "input": {"query": "multi-agent research"},
        "constraints": {"maxResults": 2},
        "outputSchema": "paper_records.v1",
    }


def command(action=TaskCommandType.Start, payload=None, sender=LEADER):
    return TaskCommand(
        id="cmd-1", sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="leader", senderId=sender, groupId="g-1",
        sessionId="session-1", taskId="task-1", command=action,
        mentions=["partner-aic"],
        dataItems=[StructuredDataItem(data=payload if payload is not None else task_payload())],
    )


class FakeClient:
    group_id = "g-1"
    is_muted = False

    def __init__(self):
        for name in ("accept_task", "start_working", "submit_for_completion", "complete_task",
                     "reject_task", "fail_task", "cancel_task", "send_task_result"):
            setattr(self, name, AsyncMock())


async def fake_backend(request):
    return {
        "schemaVersion": "0.1", "subtaskId": request.subtaskId,
        "taskType": "literature_search", "summary": "One paper", "warnings": [],
        "items": [{"kind": "paper", "data": {
            "paperId": "paper-1", "title": "Example paper",
            "doi": "10.1000/example", "url": "https://doi.org/10.1000/example",
            "pdfUrl": None, "repositoryUrls": [],
        }}],
    }


class GroupLiteratureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient()
        self.backend = AsyncMock(side_effect=fake_backend)
        self.handler = LiteratureGroupHandler(self.client, leader_aic=LEADER, executor=self.backend)

    async def test_success_and_completion(self):
        await self.handler.on_command(command(), True)
        self.client.accept_task.assert_awaited_once()
        self.client.start_working.assert_awaited_once()
        self.client.submit_for_completion.assert_awaited_once()
        p = self.client.submit_for_completion.await_args.args[2][0].dataItems[0].data
        self.assertEqual(p["schemaVersion"], "0.2")
        self.assertEqual(p["items"][0]["kind"], "paper")
        await self.handler.on_command(command(TaskCommandType.Complete), True)
        self.client.complete_task.assert_awaited_once()

    async def test_no_extra_business_fields(self):
        bad = task_payload()
        bad["requiredSkill"] = "literature.search"
        await self.handler.on_command(command(payload=bad), True)
        self.client.reject_task.assert_awaited_once()
        self.backend.assert_not_awaited()

    async def test_unmentioned_or_wrong_leader_ignored(self):
        await self.handler.on_command(command(), False)
        await self.handler.on_command(command(sender="other-aic"), True)
        self.backend.assert_not_awaited()

    async def test_duplicate_start_never_repeats_backend(self):
        await self.handler.on_command(command(), True)
        await self.handler.on_command(command(), True)
        self.assertEqual(self.backend.await_count, 1)

    async def test_backend_failure_safe(self):
        self.backend.side_effect = RuntimeError("PRIVATE_UPSTREAM_URL")
        await self.handler.on_command(command(), True)
        self.client.fail_task.assert_awaited_once_with(
            "task-1", "session-1", "LITERATURE_BACKEND_ERROR"
        )

    async def test_invitation_policy(self):
        class Obj:
            pass
        invite = Obj()
        invite.group = Obj()
        invite.group.leader = Obj()
        invite.group.leader.aic = LEADER
        invite.senderId = LEADER
        self.assertTrue(invitation_allowed(invite, allowed_leader_aic=LEADER, current_group=None))
        self.assertFalse(invitation_allowed(invite, allowed_leader_aic="untrusted", current_group=None))
        self.assertFalse(invitation_allowed(invite, allowed_leader_aic=LEADER, current_group="g-existing"))


if __name__ == "__main__":
    unittest.main()
