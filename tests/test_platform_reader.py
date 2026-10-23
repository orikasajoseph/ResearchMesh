"""Formal Reader Leader entry tests; all platform, PDF and model work is mocked."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from acps_sdk.aip import Product, StructuredDataItem, TaskCommandType, TaskResult, TaskState

import group_literature_leader as leader
import run_platform_reader as cli
from tests.test_group_invitation import LEADER, PARTNER, config


TASK = {
    "schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
    "goal": "Read one LoRA paper", "input": {"papers": [{
        "paperId": "arxiv:2106.09685", "title": "LoRA", "doi": None,
        "url": "https://arxiv.org/abs/2106.09685",
        "pdfUrl": "https://arxiv.org/pdf/2106.09685.pdf",
        "repositoryUrls": ["https://github.com/microsoft/LoRA"],
    }]}, "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
}


def result():
    paper = deepcopy(TASK["input"]["papers"][0])
    return {
        "schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
        "summary": "Offline full-text fixture", "warnings": [],
        "items": [{"kind": "paper_note", "data": {
            "source": paper, "method": "Low-rank adapters", "findings": "Fewer trainable parameters",
            "limitations": "Task coverage is limited", "analysisLevel": "fulltext",
            "evidence": [{"sourceId": paper["paperId"], "url": paper["pdfUrl"],
                          "locator": "page 3", "supports": "The method freezes base weights."}],
        }}],
    }


def message(state, data=None):
    return TaskResult(
        id="reader-" + state.value, sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="partner", senderId=PARTNER, groupId="g", taskId="t", sessionId="s",
        status={"state": state, "stateChangedAt": datetime.now(timezone.utc).isoformat()},
        products=[Product(id="notes", dataItems=[StructuredDataItem(data=data)])] if data is not None else None,
    )


class ReaderTaskTests(unittest.TestCase):
    def test_example_is_exact_single_pdf_task(self):
        path = Path(leader.__file__).parent / "examples/group_reader_single_task_v02.json"
        task = leader.validate_reader_task(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(len(task), 7)
        self.assertEqual(task["constraints"], {"maxPapers": 1})
        self.assertEqual(task["input"]["papers"][0]["pdfUrl"],
                         "https://arxiv.org/pdf/2106.09685")

    def test_rejects_wrong_task_batch_and_missing_pdf(self):
        wrong = deepcopy(TASK); wrong["taskType"] = "literature_search"
        batch = deepcopy(TASK); batch["constraints"]["maxPapers"] = 2; batch["input"]["papers"] *= 2
        missing = deepcopy(TASK); missing["input"]["papers"][0]["pdfUrl"] = None
        for task in (wrong, batch, missing):
            with self.subTest(task=task), self.assertRaises(ValueError):
                leader.validate_reader_task(task)


class ReaderConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_start_one_complete_with_fulltext_result(self):
        conversation = leader.ReaderConversation(config(), "g", "t", "s")
        record = {"startAttempted": False, "taskCommandsSent": 0, "startPublished": False,
                  "completeAttempted": False, "completePublished": False, "taskStates": [],
                  "result": None, "resultValidated": False, "protocolCompleted": False,
                  "businessReady": False}
        commands = []

        async def send(command, task_id, session_id, **kwargs):
            commands.append(command)
            if command == TaskCommandType.Start:
                await conversation.on_message(message(TaskState.Working))
                await conversation.on_message(message(TaskState.AwaitingCompletion, result()))
            else:
                await conversation.on_message(message(TaskState.Completed))

        client = AsyncMock()
        client.send_task_command.side_effect = send
        await conversation.run(client, TASK, record, lambda _: None, 1)
        self.assertEqual(commands, [TaskCommandType.Start, TaskCommandType.Complete])
        self.assertTrue(record["protocolCompleted"])
        self.assertEqual(record["result"], result())

    async def test_abstract_or_mismatched_evidence_is_rejected_as_reader_result(self):
        for change in ("abstract", "wrong-url"):
            data = result()
            if change == "abstract":
                data["items"][0]["data"]["analysisLevel"] = "abstract"
            else:
                data["items"][0]["data"]["evidence"][0]["url"] = "https://example.invalid/wrong.pdf"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "INVALID_READER_RESULT"):
                leader.ReaderConversation.extract(message(TaskState.AwaitingCompletion, data), TASK)


class ReaderWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_wrapper_selects_reader_scope_prefix_and_750_seconds(self):
        expected = {"passed": True}
        with patch.object(leader, "_run_group_single_task", new_callable=AsyncMock, return_value=expected) as run:
            actual = await leader.run_group_reader(config(), TASK)
        self.assertIs(actual, expected)
        kwargs = run.await_args.kwargs
        self.assertEqual(kwargs["task_timeout"], 750)
        self.assertEqual(kwargs["group_prefix"], "rm-reader-")
        self.assertEqual(kwargs["live_scope"], "PLATFORM_READER_SINGLE_TASK")
        self.assertEqual(kwargs["max_task_timeout"], 900)
        self.assertIs(kwargs["conversation_type"], leader.ReaderConversation)

class ReaderCliTests(unittest.TestCase):
    def test_offline_cli_writes_reader_report_without_platform(self):
        async def offline(config_value, task, **kwargs):
            self.assertFalse(kwargs["execute"])
            self.assertEqual(kwargs["task_timeout"], 750)
            record = {"scope": "OFFLINE_PLATFORM_READER_PREFLIGHT", "groupId": "g",
                      "taskId": "t", "sessionId": "s", "events": [], "result": None,
                      "passed": True, "protocolCompleted": False, "businessReady": False,
                      "cleanupSucceeded": None, "error": None, "remoteStateUnknown": False}
            kwargs["checkpoint"](record)
            return record
        task_path = Path(leader.__file__).parent / "examples/group_reader_single_task_v02.json"
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "ROOT", Path(directory)), \
                patch.object(cli, "run_group_reader", side_effect=offline), patch("builtins.print"):
            status = cli.main(["--leader-aic", LEADER, "--partner-aic", PARTNER,
                               "--credentials-dir", directory, "--task", str(task_path)])
            self.assertEqual(status, 0)
            reports = list(Path(directory).glob("outputs/platform-reader/*/report.md"))
            self.assertEqual(len(reports), 1)
            text = reports[0].read_text(encoding="utf-8")
            self.assertIn("Reader 单任务", text)
            self.assertIn("离线预检，未发送任务", text)


if __name__ == "__main__":
    unittest.main()
