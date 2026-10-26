"""Offline orchestration-core tests; no platform, provider, clone or model calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from platform_three_agent_orchestrator import (
    PlatformChainError, digest, execute_platform_chain, read_json,
)


ROOT = Path(__file__).resolve().parents[1]
PDF = "https://arxiv.org/pdf/2106.09685"
REPOSITORY = "https://github.com/microsoft/LoRA"


def plan():
    return json.loads((ROOT / "examples/platform_three_agent_chain_v02.json").read_text(encoding="utf-8"))


def paper():
    return {"paperId": "arxiv:2106.09685", "title": "LoRA: Low-Rank Adaptation of Large Language Models",
            "doi": None, "url": "https://arxiv.org/abs/2106.09685", "pdfUrl": PDF,
            "repositoryUrls": [REPOSITORY]}


def stage_result(task):
    common = {"schemaVersion": "0.2", "subtaskId": task["subtaskId"],
              "taskType": task["taskType"], "warnings": []}
    if task["taskType"] == "literature_search":
        return {**common, "summary": "Offline Literature contract fixture",
                "items": [{"kind": "paper", "data": paper()}]}
    if task["taskType"] == "paper_read":
        return {**common, "summary": "Offline Reader contract fixture",
                "items": [{"kind": "paper_note", "data": {
                    "source": deepcopy(task["input"]["papers"][0]), "method": "Low-rank adapters",
                    "findings": "Reduced trainable parameters", "limitations": "Selected tasks only",
                    "analysisLevel": "fulltext", "evidence": [{"sourceId": "arxiv:2106.09685",
                        "url": PDF, "locator": "page 3", "supports": "Base weights are frozen."}],
                }}]}
    return {**common, "summary": "Offline Repository contract fixture",
            "items": [{"kind": "repository", "data": {
                "url": REPOSITORY, "license": "UNKNOWN", "isMock": False,
                "relationEvidence": "Repository URL propagated from the selected paper source.",
                "evidence": [{"sourceId": "E1", "url": REPOSITORY + "/blob/" + "a" * 40 + "/README.md#L1",
                              "locator": "README.md:L1", "supports": "README describes the project."}],
            }}]}


def accepted_record(stage, task):
    result = stage_result(task)
    return {"scope": "OFFLINE_FORMAL_CONTRACT_FIXTURE", "task": deepcopy(task), "result": result,
            "passed": True, "protocolCompleted": True, "businessReady": True,
            "resultValidated": True, "cleanupSucceeded": True, "remoteStateUnknown": False,
            "error": None, "groupId": "group-" + stage, "sessionId": "session-" + stage,
            "taskId": "task-" + stage, "partnerAic": "aic-" + stage}


class FakeRunner:
    def __init__(self, fail=None):
        self.calls = []
        self.payloads = []
        self.fail = fail

    async def __call__(self, stage, task):
        self.calls.append(stage)
        self.payloads.append(deepcopy(task))
        if stage == self.fail:
            value = accepted_record(stage, task)
            value.update(passed=False, protocolCompleted=False, businessReady=False,
                         resultValidated=False, cleanupSucceeded=True,
                         remoteStateUnknown=True, error={"code": "REMOTE_STATE_UNKNOWN"})
            return value
        return accepted_record(stage, task)


class PlatformThreeAgentOrchestratorTests(unittest.TestCase):
    def test_success_binds_real_outputs_uses_distinct_groups_and_resume_is_noop(self):
        with TemporaryDirectory() as directory:
            runner = FakeRunner()
            first = asyncio.run(execute_platform_chain(plan(), directory, runner))
            second = asyncio.run(execute_platform_chain(plan(), directory, runner))
            self.assertEqual(runner.calls, list(("literature_search", "paper_read", "repository_analyze")))
            self.assertEqual(first["state"], "completed")
            self.assertEqual(second["state"], "completed")
            self.assertEqual(first["automaticRetries"], 0)
            self.assertFalse(first["formalPlatformExecuted"])
            reader = runner.payloads[1]
            repository = runner.payloads[2]
            self.assertEqual(reader["input"]["papers"], [paper()])
            self.assertEqual(repository["input"]["papers"], [paper()])
            self.assertEqual(repository["input"]["paperNotes"][0]["method"], "Low-rank adapters")
            self.assertEqual(repository["input"]["repositoryUrls"], [REPOSITORY])
            self.assertEqual(len({row["groupId"] for row in first["stages"]}), 3)
            self.assertEqual(len({row["taskId"] for row in first["stages"]}), 3)
            self.assertIn("completed", Path(directory, "report.md").read_text(encoding="utf-8"))

    def test_uncertain_reader_is_never_automatically_replayed(self):
        with TemporaryDirectory() as directory:
            runner = FakeRunner(fail="paper_read")
            with self.assertRaisesRegex(PlatformChainError, "FORMAL_STAGE_NOT_ACCEPTED"):
                asyncio.run(execute_platform_chain(plan(), directory, runner))
            self.assertEqual(runner.calls, ["literature_search", "paper_read"])
            with self.assertRaisesRegex(PlatformChainError, "FORMAL_STAGE_NOT_SAFE_TO_REPLAY_PAPER_READ"):
                asyncio.run(execute_platform_chain(plan(), directory, runner))
            self.assertEqual(runner.calls, ["literature_search", "paper_read"])
            state = read_json(Path(directory) / "state.json")
            self.assertEqual(state["state"], "blocked_uncertain")
            self.assertTrue(Path(directory, "stages/paper_read/record.json").is_file())
            self.assertNotIn("repository_analyze", runner.calls)

    def test_tampered_completed_result_stops_before_next_runner_call(self):
        with TemporaryDirectory() as directory:
            runner = FakeRunner()
            asyncio.run(execute_platform_chain(plan(), directory, runner))
            path = Path(directory) / "stages/literature_search/result.json"
            value = read_json(path); value["summary"] = "tampered"
            path.write_text(json.dumps(value), encoding="utf-8")
            count = len(runner.calls)
            with self.assertRaisesRegex(PlatformChainError, "FORMAL_STAGE_CHECKPOINT_CHANGED"):
                asyncio.run(execute_platform_chain(plan(), directory, runner))
            self.assertEqual(len(runner.calls), count)

    def test_changed_plan_is_rejected_without_stage_calls(self):
        with TemporaryDirectory() as directory:
            runner = FakeRunner()
            asyncio.run(execute_platform_chain(plan(), directory, runner))
            changed = plan(); changed["userQuery"] += " changed"
            with self.assertRaisesRegex(PlatformChainError, "FORMAL_CHAIN_PLAN_CHANGED"):
                asyncio.run(execute_platform_chain(changed, directory, runner))
            self.assertEqual(len(runner.calls), 3)

    def test_unsafe_constraints_are_rejected_before_runner(self):
        for change in ("many", "execute", "search", "no-notes"):
            value = plan()
            if change == "many": value["tasks"][0]["constraints"]["maxResults"] = 2
            elif change == "execute": value["tasks"][2]["constraints"]["executeCode"] = True
            elif change == "search": value["tasks"][2]["constraints"]["allowRepositorySearch"] = True
            else: value["tasks"][2]["constraints"]["requirePaperNotes"] = False
            with self.subTest(change=change), TemporaryDirectory() as directory:
                runner = FakeRunner()
                with self.assertRaises(ValueError):
                    asyncio.run(execute_platform_chain(value, directory, runner))
                self.assertEqual(runner.calls, [])

    def test_canonical_hash_is_key_order_independent(self):
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
