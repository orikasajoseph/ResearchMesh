"""Offline orchestration-core tests; no platform, provider, clone or model calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from platform_three_agent_orchestrator import (
    PlatformChainError, digest, execute_platform_chain, read_json,
    validate_completed_record,
)
from repository_paper_context import attach_comparison


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
    result = {**common, "summary": "Offline Repository contract fixture",
            "items": [{"kind": "repository", "data": {
                "url": REPOSITORY, "license": "UNKNOWN", "isMock": False,
                "relationEvidence": "Repository URL propagated from the selected paper source.",
                "evidence": [{"sourceId": "E1", "url": REPOSITORY + "/blob/" + "a" * 40 + "/README.md#L1",
                              "locator": "README.md:L1", "supports": "README describes the project."}],
            }}]}
    return attach_comparison(result, task, task["input"]["paperNotes"])


def accepted_record(stage, task):
    result = stage_result(task)
    return {"scope": "OFFLINE_FORMAL_CONTRACT_FIXTURE", "task": deepcopy(task), "result": result,
            "passed": True, "protocolCompleted": True, "businessReady": True,
            "resultValidated": True, "cleanupSucceeded": True, "remoteStateUnknown": False,
            "error": None, "groupId": "group-" + stage, "sessionId": "session-" + stage,
            "taskId": "task-" + stage, "partnerAic": "aic-" + stage}


def cleanup_failed_business_record(task):
    value = accepted_record("literature_search", task)
    value.update(
        leaderAic="leader-aic", passed=False, cleanupSucceeded=False,
        partnerDisbandConfirmed=False, aclDeleted=False, groupResourcesDeleted=False,
        startAttempted=True, startPublished=True, completeAttempted=True,
        completePublished=True, taskCommandsSent=2, cleanupAttempted=True,
        disbandPublished=True, connectionClosed=True,
        cleanupErrors=[
            {"step": "partner_leave_confirmation", "type": "TimeoutError"},
            {"step": "stop_consumer_deferred", "type": "DependencyFailed"},
            {"step": "delete_group_resources", "type": "IncompleteCleanup"},
            {"step": "delete_own_acl_deferred", "type": "DependencyFailed"},
        ],
    )
    return value


def cleanup_protocol_proof():
    return {
        "scope": "PLATFORM_INVITATION_ONLY", "passed": True,
        "leaderAic": "leader-aic", "partnerAic": "aic-literature_search",
        "groupId": "proof-group", "cleanupSessionId": "proof-session",
        "disbandCommandId": "proof-disband", "partnerJoinConfirmed": True,
        "cleanupSucceeded": True, "partnerDisbandConfirmed": True,
        "aclDeleted": True, "groupResourcesDeleted": True, "connectionClosed": True,
        "taskCommandsSent": 0, "llmCalls": 0, "cleanupErrors": [], "error": None,
        "partnerLeaveReceipt": {
            "groupId": "proof-group", "sessionId": "proof-session",
            "disbandCommandId": "proof-disband", "senderId": "aic-literature_search",
        },
    }


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
    def test_completed_business_checkpoint_can_resume_without_replaying_s1(self):
        with TemporaryDirectory() as directory:
            first_calls = []
            async def first_runner(stage, task):
                first_calls.append(stage)
                return cleanup_failed_business_record(task)
            with self.assertRaisesRegex(PlatformChainError, "FORMAL_STAGE_NOT_ACCEPTED"):
                asyncio.run(execute_platform_chain(plan(), directory, first_runner))
            source = Path(directory, "stages/literature_search/record.json")
            source_hash = digest(read_json(source))
            runner = FakeRunner()
            state = asyncio.run(execute_platform_chain(
                plan(), directory, runner, resume_business_completed=True,
                business_cleanup_proof=cleanup_protocol_proof()))
            self.assertEqual(first_calls, ["literature_search"])
            self.assertEqual(runner.calls, ["paper_read", "repository_analyze"])
            self.assertEqual(state["state"], "completed_with_recovered_checkpoint")
            self.assertFalse(state["formalPlatformExecuted"])
            self.assertEqual(digest(read_json(source)), source_hash)
            row = state["stages"][0]
            self.assertEqual(row["state"], "business_checkpoint_recovered")
            self.assertFalse(row["originalCleanupResolved"])
            recovery = read_json(Path(directory,
                "stages/literature_search/business_checkpoint_recovery.json"))
            self.assertFalse(recovery["originalCleanupResolved"])
            self.assertIn("not an uninterrupted", recovery["claimBoundary"])

    def test_business_checkpoint_recovery_rejects_invalid_proof_before_s2(self):
        with TemporaryDirectory() as directory:
            async def first_runner(stage, task):
                return cleanup_failed_business_record(task)
            with self.assertRaises(PlatformChainError):
                asyncio.run(execute_platform_chain(plan(), directory, first_runner))
            runner = FakeRunner()
            proof = cleanup_protocol_proof(); proof["partnerDisbandConfirmed"] = False
            with self.assertRaisesRegex(PlatformChainError,
                    "BUSINESS_CHECKPOINT_CLEANUP_PROOF_INVALID"):
                asyncio.run(execute_platform_chain(
                    plan(), directory, runner, resume_business_completed=True,
                    business_cleanup_proof=proof))
            self.assertEqual(runner.calls, [])

    def test_business_checkpoint_recovery_rejects_missing_result_and_uncertain_cleanup(self):
        for change, expected in (("missing_result", "BUSINESS_CHECKPOINT_RESULT_INVALID"),
                                 ("remote_unknown", "BUSINESS_CHECKPOINT_RECOVERY_NOT_ALLOWED")):
            with self.subTest(change=change), TemporaryDirectory() as directory:
                async def first_runner(stage, task):
                    return cleanup_failed_business_record(task)
                with self.assertRaises(PlatformChainError):
                    asyncio.run(execute_platform_chain(plan(), directory, first_runner))
                record_path = Path(directory, "stages/literature_search/record.json")
                state_path = Path(directory, "state.json")
                record, state = read_json(record_path), read_json(state_path)
                if change == "missing_result":
                    record["result"] = None
                else:
                    record["remoteStateUnknown"] = True
                record_path.write_text(json.dumps(record), encoding="utf-8")
                state["stages"][0]["recordSha256"] = digest(record)
                state_path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaisesRegex((PlatformChainError, ValueError, TypeError), expected):
                    asyncio.run(execute_platform_chain(
                        plan(), directory, FakeRunner(), resume_business_completed=True,
                        business_cleanup_proof=cleanup_protocol_proof()))

    def test_business_checkpoint_recovery_rejects_task_and_record_hash_mismatch(self):
        for target, expected in (("task", "FORMAL_STAGE_TASK_CHANGED"),
                                 ("record", "FORMAL_STAGE_CHECKPOINT_CHANGED")):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                async def first_runner(stage, task):
                    return cleanup_failed_business_record(task)
                with self.assertRaises(PlatformChainError):
                    asyncio.run(execute_platform_chain(plan(), directory, first_runner))
                path = Path(directory, "stages/literature_search",
                            "task.json" if target == "task" else "record.json")
                value = read_json(path)
                if target == "task":
                    value["goal"] += " tampered"
                else:
                    value["result"]["summary"] = "tampered"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(PlatformChainError, expected):
                    asyncio.run(execute_platform_chain(
                        plan(), directory, FakeRunner(), resume_business_completed=True,
                        business_cleanup_proof=cleanup_protocol_proof()))

    def test_recovered_checkpoint_is_immutable_on_later_resume(self):
        with TemporaryDirectory() as directory:
            async def first_runner(stage, task):
                return cleanup_failed_business_record(task)
            with self.assertRaises(PlatformChainError):
                asyncio.run(execute_platform_chain(plan(), directory, first_runner))
            runner = FakeRunner()
            asyncio.run(execute_platform_chain(
                plan(), directory, runner, resume_business_completed=True,
                business_cleanup_proof=cleanup_protocol_proof()))
            result_path = Path(directory, "stages/literature_search/result.json")
            changed = read_json(result_path); changed["summary"] = "tampered"
            result_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(PlatformChainError,
                    "BUSINESS_CHECKPOINT_RECOVERY_CHANGED"):
                asyncio.run(execute_platform_chain(plan(), directory, FakeRunner()))

    def test_duplicate_recovery_is_a_noop_and_proof_is_immutable(self):
        with TemporaryDirectory() as directory:
            async def first_runner(stage, task):
                return cleanup_failed_business_record(task)
            with self.assertRaises(PlatformChainError):
                asyncio.run(execute_platform_chain(plan(), directory, first_runner))
            runner = FakeRunner()
            first = asyncio.run(execute_platform_chain(
                plan(), directory, runner, resume_business_completed=True,
                business_cleanup_proof=cleanup_protocol_proof()))
            recovery = Path(directory,
                "stages/literature_search/business_checkpoint_recovery.json")
            proof = Path(directory, "stages/literature_search/cleanup_protocol_proof.json")
            before = (recovery.read_bytes(), proof.read_bytes())
            forbidden = FakeRunner()
            second = asyncio.run(execute_platform_chain(
                plan(), directory, forbidden, resume_business_completed=True,
                business_cleanup_proof=cleanup_protocol_proof()))
            self.assertEqual(first["state"], "completed_with_recovered_checkpoint")
            self.assertEqual(second["state"], "completed_with_recovered_checkpoint")
            self.assertEqual(forbidden.calls, [])
            self.assertEqual((recovery.read_bytes(), proof.read_bytes()), before)
            self.assertEqual(second["recoveredStages"], ["literature_search"])

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

    def test_repository_record_rejects_missing_or_tampered_paper_comparison(self):
        value = plan()
        search = stage_result(value["tasks"][0])
        reader_task = deepcopy(value["tasks"][1])
        reader_task["input"]["papers"] = [paper()]
        reader = stage_result(reader_task)
        repository_task = deepcopy(value["tasks"][2])
        repository_task["input"].update(
            papers=[paper()],
            paperNotes=[deepcopy(reader["items"][0]["data"])],
            repositoryUrls=[REPOSITORY])
        valid = accepted_record("repository_analyze", repository_task)
        self.assertEqual(validate_completed_record(valid, repository_task), valid["result"])
        for change in ("missing", "tampered"):
            record = deepcopy(valid)
            comparison = record["result"]["items"][0]["data"].get("paperComparison")
            if change == "missing":
                record["result"]["items"][0]["data"].pop("paperComparison")
            else:
                comparison["papers"][0]["noteSha256"] = "0" * 64
            with self.subTest(change=change), self.assertRaisesRegex(
                    PlatformChainError, "FORMAL_REPOSITORY_COMPARISON_INVALID"):
                validate_completed_record(record, repository_task)


if __name__ == "__main__":
    unittest.main()
