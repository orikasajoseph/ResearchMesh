"""No-API dispatch regression plus one optional official-SDK local roundtrip."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from planner_contract import validate_plan
from three_agent_dispatch_v02 import dispatch_plan
from run_three_agent_aip_v02 import offline_paper_record

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "three_agent_local_fixture_v02.json"


def plan():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def outcome(task, *, no_reader_items=True):
    kind = task["taskType"]
    if kind == "literature_search":
        result = offline_paper_record(task["subtaskId"])
        result["schemaVersion"] = "0.2"
    elif kind == "paper_read":
        result = {
            "schemaVersion": "0.2", "subtaskId": task["subtaskId"],
            "taskType": kind, "summary": "No full text available; no analysis produced.",
            "items": [] if no_reader_items else [{"kind": "paper_note", "data": {
                "source": task["input"]["papers"][0],
                "method": "Fixture, not real", "findings": "Fixture, not real",
                "limitations": "Fixture, not real", "isMock": True,
            }}],
            "warnings": ["FULL_TEXT_UNAVAILABLE"] if no_reader_items else ["OFFLINE_FIXTURE"],
        }
    else:
        result = {
            "schemaVersion": "0.2", "subtaskId": task["subtaskId"],
            "taskType": kind, "summary": "Static local fixture only",
            "items": [{"kind": "repository", "data": {
                "url": "https://github.com/pypa/sampleproject",
                "license": "UNKNOWN", "relationEvidence": "Not verified",
                "isMock": True, "evidence": [],
            }}], "warnings": ["OFFLINE_FIXTURE"],
        }
    return {
        "status": "completed", "protocolCompleted": True,
        "businessValidated": True, "researchSucceeded": False,
        "isFixture": True, "result": result, "errorCode": None,
    }


class ThreeAgentDispatchUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_is_valid(self):
        self.assertIsNone(validate_plan(plan()))

    async def test_three_agents_dispatched_and_bound_in_order(self):
        calls = []
        async def invoke(task):
            calls.append(deepcopy(task))
            return outcome(task)
        result = await dispatch_plan(plan(), invoke)
        self.assertEqual([c["subtaskId"] for c in calls], ["S1", "S2", "S3"])
        self.assertEqual(calls[1]["input"]["papers"][0]["paperId"], "OFFLINE-PAPER-ONLY")
        self.assertEqual(calls[2]["input"]["repositoryUrls"],
                         ["https://github.com/pypa/sampleproject"])
        self.assertEqual([r["status"] for r in result["records"]],
                         ["completed", "completed", "completed"])
        self.assertFalse(result["records"][1]["dependencyUsable"])
        self.assertTrue(result["records"][2]["dependencyUsable"])
        self.assertFalse(result["pipelineResearchSucceeded"])
        self.assertFalse(result["platformConnected"])

    async def test_reader_no_pdf_blocks_its_dependent_repository(self):
        p = plan()
        p["tasks"][2]["dependsOn"] = ["S2"]
        p["tasks"][2]["constraints"]["requirePaperNotes"] = True
        calls = []
        async def invoke(task):
            calls.append(task["subtaskId"])
            return outcome(task)
        run = await dispatch_plan(p, invoke)
        self.assertEqual(calls, ["S1", "S2"])
        self.assertEqual(run["records"][2]["errorCode"], "DEPENDENCY_NOT_USABLE")
        self.assertIn("S1", run["results"])
        self.assertIn("S2", run["results"])

    async def test_protocol_failed_prevents_downstream_call(self):
        p = plan()
        calls = []
        async def invoke(task):
            calls.append(task["subtaskId"])
            x = outcome(task)
            if task["subtaskId"] == "S1":
                x["protocolCompleted"] = False
                x["status"] = "failed"
            return x
        run = await dispatch_plan(p, invoke)
        self.assertEqual(calls, ["S1"])
        self.assertEqual(run["records"][1]["status"], "skipped")
        self.assertEqual(run["records"][2]["status"], "skipped")

    async def test_invalid_correlation_is_not_dispatched_downstream(self):
        async def invoke(task):
            x = outcome(task)
            if task["subtaskId"] == "S1":
                x["result"]["subtaskId"] = "WRONG"
            return x
        run = await dispatch_plan(plan(), invoke)
        self.assertEqual(run["records"][0]["errorCode"], "DISPATCH_OR_CONTRACT_ERROR")
        self.assertEqual(run["records"][1]["status"], "skipped")

    async def test_incorrect_skill_is_rejected_before_network(self):
        p = plan()
        p["tasks"][1]["requiredSkill"] = "repository.analyze"
        with self.assertRaises(ValueError):
            await dispatch_plan(p, lambda x: None)

    async def test_clarification_plan_never_invokes_aip(self):
        p = {
            "schemaVersion": "0.2", "userQuery": "Need research",
            "needsClarification": True, "clarificationQuestion": "Which topic?", "tasks": [],
        }
        async def fail(_):
            raise AssertionError("AIP should never be called")
        out = await dispatch_plan(p, fail)
        self.assertEqual(out["records"], [])

    async def test_fixture_never_reports_true_research_success(self):
        async def invoke(task):
            x = outcome(task)
            x["researchSucceeded"] = True
            return x
        run = await dispatch_plan(plan(), invoke)
        self.assertFalse(run["pipelineResearchSucceeded"])
        self.assertFalse(any(r["researchSucceeded"] for r in run["records"]))


@unittest.skipUnless(importlib.util.find_spec("acps_sdk"), "ACPS SDK only installed in local Windows venv")
class ThreeAgentActualSdkRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_offline_aip_roundtrips(self):
        from run_three_agent_aip_v02 import run_fixture_plan
        run = await run_fixture_plan(plan())
        records = run["records"]
        self.assertEqual([x["status"] for x in records], ["completed"] * 3)
        self.assertTrue(all(x["protocolCompleted"] and x["businessValidated"] for x in records))
        self.assertTrue(records[0]["dependencyUsable"])
        self.assertFalse(records[1]["dependencyUsable"])
        self.assertTrue(records[2]["dependencyUsable"])
        self.assertFalse(run["pipelineResearchSucceeded"])


if __name__ == "__main__":
    unittest.main()
