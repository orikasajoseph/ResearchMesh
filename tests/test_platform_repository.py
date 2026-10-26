"""Formal Repository Leader entry tests; no platform, clone, CodeWiki or LLM calls."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from acps_sdk.aip import Product, StructuredDataItem, TaskCommandType, TaskResult, TaskState

import group_literature_leader as leader
import run_platform_repository as cli
from repository_adapter import normalize_repository_result
from tests.test_group_invitation import LEADER, PARTNER, config


TASK = {
    "schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
    "goal": "Static analysis of one explicit repository",
    "input": {"repositoryUrls": ["https://github.com/pypa/sampleproject"]},
    "constraints": {"maxRepositories": 1, "executeCode": False,
                    "allowRepositorySearch": False, "requirePaperNotes": False},
    "outputSchema": "code_records.v1",
}


def native_result():
    return {
        "schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
        "summary": "Offline static repository fixture", "warnings": [],
        "items": [{"kind": "repository_record", "data": {
            "repository": {"url": "https://github.com/pypa/sampleproject.git", "commitHash": "a" * 40},
            "structure": [], "dependencies": [], "entrypoints": [], "runCommands": [],
            "reproductionBarriers": [],
            "overview": {"text": "README describes the package.", "evidenceIds": ["E1"]},
            "diagnostics": {"provider": "codewiki-static"},
            "evidence": [{"evidenceId": "E1", "filePath": "README.md", "sourceType": "readme",
                          "symbol": None, "startLine": 1, "endLine": 2,
                          "text": "Sample package", "supports": ["structure"]}],
        }}],
    }


WRAPPER = SimpleNamespace(RepositoryAnalyzeResult=SimpleNamespace(
    model_validate=lambda raw: SimpleNamespace(model_dump=lambda **kwargs: deepcopy(raw))))


def result():
    return normalize_repository_result(TASK, native_result(), WRAPPER)


def message(state, data=None, *, message_id=None):
    return TaskResult(
        id=message_id or "repository-" + state.value,
        sentAt=datetime.now(timezone.utc).isoformat(), senderRole="partner", senderId=PARTNER,
        groupId="g", taskId="t", sessionId="s",
        status={"state": state, "stateChangedAt": datetime.now(timezone.utc).isoformat()},
        products=[Product(id="repositories", dataItems=[StructuredDataItem(data=data)])]
        if data is not None else None,
    )


class RepositoryTaskTests(unittest.TestCase):
    def test_example_is_exact_static_single_repository_task(self):
        path = Path(leader.__file__).parent / "examples/group_repository_single_task_v02.json"
        task = leader.validate_repository_task(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(len(task), 7)
        self.assertEqual(task["input"], {"repositoryUrls": ["https://github.com/pypa/sampleproject"]})
        self.assertEqual(task["constraints"], TASK["constraints"])

    def test_rejects_wrong_batch_dynamic_modes_and_paper_context(self):
        cases = []
        wrong = deepcopy(TASK); wrong["taskType"] = "paper_read"; cases.append(wrong)
        batch = deepcopy(TASK); batch["constraints"]["maxRepositories"] = 2
        batch["input"]["repositoryUrls"].append("https://github.com/pypa/pip"); cases.append(batch)
        execute = deepcopy(TASK); execute["constraints"]["executeCode"] = True; cases.append(execute)
        search = deepcopy(TASK); search["constraints"]["allowRepositorySearch"] = True; cases.append(search)
        notes = deepcopy(TASK); notes["constraints"]["requirePaperNotes"] = True
        notes["input"]["paperNotes"] = [{"summary": "not allowed in first platform check"}]; cases.append(notes)
        for task in cases:
            with self.subTest(task=task), self.assertRaises(ValueError):
                leader.validate_repository_task(task)

    def test_contextual_chain_task_requires_one_paper_note_and_repository(self):
        contextual = deepcopy(TASK)
        contextual["constraints"]["requirePaperNotes"] = True
        contextual["input"].update(
            papers=[{"paperId": "arxiv:2106.09685", "title": "LoRA", "doi": None,
                     "url": "https://arxiv.org/abs/2106.09685",
                     "pdfUrl": "https://arxiv.org/pdf/2106.09685",
                     "repositoryUrls": ["https://github.com/pypa/sampleproject"]}],
            paperNotes=[{"source": {"paperId": "arxiv:2106.09685", "title": "LoRA", "doi": None,
                                    "url": "https://arxiv.org/abs/2106.09685",
                                    "pdfUrl": "https://arxiv.org/pdf/2106.09685",
                                    "repositoryUrls": ["https://github.com/pypa/sampleproject"]},
                         "method": "method", "findings": "findings", "limitations": "limitations",
                         "analysisLevel": "fulltext", "evidence": [{
                             "sourceId": "arxiv:2106.09685", "url": "https://arxiv.org/pdf/2106.09685",
                             "locator": "page 3", "supports": "The base weights are frozen."}]}])
        self.assertEqual(leader.validate_repository_chain_task(contextual), contextual)
        missing = deepcopy(contextual); missing["input"]["paperNotes"] = []
        with self.assertRaises(ValueError):
            leader.validate_repository_chain_task(missing)


class RepositoryConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_start_one_complete_and_duplicate_result_is_idempotent(self):
        conversation = leader.RepositoryConversation(config(), "g", "t", "s")
        record = {"startAttempted": False, "taskCommandsSent": 0, "startPublished": False,
                  "completeAttempted": False, "completePublished": False, "taskStates": [],
                  "result": None, "resultValidated": False, "protocolCompleted": False,
                  "businessReady": False}
        commands = []

        async def send(command, task_id, session_id, **kwargs):
            commands.append(command)
            if command == TaskCommandType.Start:
                await conversation.on_message(message(TaskState.Working))
                value = result()
                await conversation.on_message(message(TaskState.AwaitingCompletion, value,
                                                      message_id="same-result"))
                await conversation.on_message(message(TaskState.AwaitingCompletion, value,
                                                      message_id="same-result"))
            else:
                await conversation.on_message(message(TaskState.Completed))

        client = AsyncMock()
        client.send_task_command.side_effect = send
        await conversation.run(client, TASK, record, lambda _: None, 1)
        self.assertEqual(commands, [TaskCommandType.Start, TaskCommandType.Complete])
        self.assertTrue(record["protocolCompleted"])
        self.assertEqual(record["result"], result())

    def test_mismatched_repository_and_requested_enrichment_are_rejected(self):
        for change in ("wrong-url", "enhanced"):
            data = result()
            if change == "wrong-url":
                data["items"][0]["data"]["url"] = "https://github.com/pypa/pip"
            else:
                data["items"][0]["data"]["enrichment"] = {"requested": True, "status": "completed"}
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "INVALID_REPOSITORY_RESULT"):
                leader.RepositoryConversation.extract(message(TaskState.AwaitingCompletion, data), TASK)


class RepositoryWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_repository_wrapper_selects_static_scope_and_300_seconds(self):
        expected = {"passed": True}
        with patch.object(leader, "_run_group_single_task", new_callable=AsyncMock,
                          return_value=expected) as run:
            actual = await leader.run_group_repository(config(), TASK)
        self.assertIs(actual, expected)
        kwargs = run.await_args.kwargs
        self.assertEqual(kwargs["task_timeout"], 300)
        self.assertEqual(kwargs["group_prefix"], "rm-repository-")
        self.assertEqual(kwargs["live_scope"], "PLATFORM_REPOSITORY_SINGLE_TASK")
        self.assertEqual(kwargs["max_task_timeout"], 600)
        self.assertIs(kwargs["conversation_type"], leader.RepositoryConversation)

    async def test_contextual_repository_wrapper_selects_chain_scope(self):
        expected = {"passed": True}
        with patch.object(leader, "_run_group_single_task", new_callable=AsyncMock,
                          return_value=expected) as run:
            actual = await leader.run_group_repository_chain(config(), TASK)
        self.assertIs(actual, expected)
        kwargs = run.await_args.kwargs
        self.assertIs(kwargs["task_validator"], leader.validate_repository_chain_task)
        self.assertEqual(kwargs["group_prefix"], "rm-repository-chain-")
        self.assertEqual(kwargs["live_scope"], "PLATFORM_REPOSITORY_CHAIN_TASK")


class RepositoryCliTests(unittest.TestCase):
    def test_offline_cli_writes_repository_report_without_platform(self):
        async def offline(config_value, task, **kwargs):
            self.assertFalse(kwargs["execute"])
            self.assertEqual(kwargs["task_timeout"], 300)
            record = {"scope": "OFFLINE_PLATFORM_REPOSITORY_PREFLIGHT", "groupId": "g",
                      "taskId": "t", "sessionId": "s", "events": [], "result": None,
                      "passed": True, "protocolCompleted": False, "businessReady": False,
                      "cleanupSucceeded": None, "error": None, "remoteStateUnknown": False}
            kwargs["checkpoint"](record)
            return record

        task_path = Path(leader.__file__).parent / "examples/group_repository_single_task_v02.json"
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "ROOT", Path(directory)), \
                patch.object(cli, "run_group_repository", side_effect=offline), patch("builtins.print"):
            status = cli.main(["--leader-aic", LEADER, "--partner-aic", PARTNER,
                               "--credentials-dir", directory, "--task", str(task_path)])
            self.assertEqual(status, 0)
            reports = list(Path(directory).glob("outputs/platform-repository/*/report.md"))
            self.assertEqual(len(reports), 1)
            text = reports[0].read_text(encoding="utf-8")
            self.assertIn("Repository 单任务", text)
            self.assertIn("离线预检，未发送任务", text)


if __name__ == "__main__":
    unittest.main()
