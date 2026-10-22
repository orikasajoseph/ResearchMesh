"""Offline project release, audit aggregation and formal-entry regressions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from group_literature_leader import validate_platform_task
from group_three_agent_orchestrator import STAGE_TIMEOUTS, dispatch_group_plan
from scripts.audit_release_history import scan_text
from scripts.validate_amp_configs import validate_directory
from tests.test_group_repository_platform import task_payload
from tests.test_three_agent_aip_v02 import outcome, plan
from verify_project_acceptance import TASK_TYPES, verify_project
from verify_reader_aip import fixture_task

ROOT = Path(__file__).resolve().parents[1]


class ProjectReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def manifest(self):
        runs = []
        for index, task_type in enumerate(TASK_TYPES, 1):
            journal = self.root / f"{task_type}.jsonl"
            journal.write_text("{}\n", encoding="utf-8")
            runs.append({
                "taskType": task_type, "journal": str(journal),
                "database": str(self.root / "tasks.sqlite3"),
                "group": f"group-{index}", "session": f"session-{index}",
                "task": f"task-{index}", "agent": f"agent-{index}",
            })
        return {"schemaVersion": "researchmesh.formal-acceptance.v1", "runs": runs}

    def test_project_acceptance_requires_three_fresh_accepted_runs(self):
        def accepted(journal, database, **kwargs):
            return {"accepted": True, "runId": Path(journal).stem,
                    "checks": {"test": True}}
        report = verify_project(self.manifest(), verifier=accepted)
        self.assertTrue(report["accepted"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(set(report["agentReports"]), set(TASK_TYPES))
        manifest = self.manifest()
        manifest["runs"][2]["task"] = manifest["runs"][1]["task"]
        self.assertFalse(verify_project(manifest, verifier=accepted)["accepted"])

    def test_project_acceptance_propagates_individual_failure(self):
        calls = []
        def verifier(journal, database, **kwargs):
            calls.append(kwargs)
            return {"accepted": kwargs["expected_task_type"] != "paper_read", "runId": "run"}
        report = verify_project(self.manifest(), verifier=verifier)
        self.assertFalse(report["accepted"])
        self.assertTrue(calls[0]["require_http"])
        self.assertFalse(calls[1]["require_http"])

    def test_all_amp_configs_are_distinct_and_complete(self):
        report = validate_directory(ROOT / "config" / "amp")
        self.assertTrue(report["passed"], report)
        self.assertEqual([r["agent"] for r in report["records"]],
                         ["literature", "reader", "repository"])

    def test_history_scanner_never_returns_matched_content(self):
        secret = "Author" + "ization: Bearer " + "abcdefghijklmnop"
        findings = scan_text("safe=1\n" + secret + "\n")
        self.assertEqual(findings, [{"pattern": "bearer_token", "line": 2}])
        self.assertNotIn("abcdefghijklmnop", json.dumps(findings))

    def test_analysis_leader_validates_reader_and_repository_before_network(self):
        reader = validate_platform_task(fixture_task(), "paper_read")
        repository = validate_platform_task(task_payload(), "repository_analyze")
        self.assertEqual(reader["taskType"], "paper_read")
        self.assertEqual(repository["taskType"], "repository_analyze")
        missing = fixture_task()
        missing["input"]["papers"][0]["pdfUrl"] = None
        with self.assertRaisesRegex(Exception, "INVALID_PLATFORM_ANALYSIS_TASK"):
            validate_platform_task(missing, "paper_read")

    def test_three_group_orchestrator_binds_in_order_and_uses_stage_budgets(self):
        calls = []
        async def stage(task, timeout):
            calls.append((deepcopy(task), timeout))
            fixture = outcome(task, no_reader_items=False)
            return {
                "scope": "OFFLINE_PLATFORM_PREFLIGHT",
                "groupId": "group-" + task["subtaskId"],
                "sessionId": "session-" + task["subtaskId"],
                "taskId": "task-" + task["subtaskId"],
                "partnerAic": "agent-" + task["subtaskId"],
                "passed": True, "protocolCompleted": True,
                "resultValidated": True, "result": fixture["result"], "error": None,
            }
        result = asyncio.run(dispatch_group_plan(plan(), stage, require_formal=False))
        self.assertEqual([c[0]["subtaskId"] for c in calls], ["S1", "S2", "S3"])
        self.assertEqual([c[1] for c in calls], [STAGE_TIMEOUTS[t] for t in TASK_TYPES])
        self.assertTrue(result["freshDistinctStageIdentifiers"])
        self.assertFalse(result["formalEvidenceEligible"])
        self.assertFalse(result["pipelineResearchSucceeded"])

    def test_analysis_leader_cli_default_is_offline(self):
        import run_platform_analysis as cli
        task = self.root / "task.json"
        task.write_text(json.dumps(fixture_task()), encoding="utf-8")
        record = {"passed": True, "protocolCompleted": False,
                  "businessReady": False, "remoteStateUnknown": False}
        def finish(coro):
            coro.close()
            return record
        with patch.object(cli, "ROOT", self.root), \
             patch.object(cli, "asyncio") as async_module, \
             patch.object(cli, "run_group_task", new=AsyncMock(return_value=record)) as run:
            async_module.run.side_effect = finish
            code = cli.main(["--task-type", "paper_read", "--leader-aic", "leader",
                             "--partner-aic", "reader", "--credentials-dir", str(self.root),
                             "--task", str(task)])
        self.assertEqual(code, 0)
        self.assertFalse(run.call_args.kwargs["execute"])


if __name__ == "__main__":
    unittest.main()
