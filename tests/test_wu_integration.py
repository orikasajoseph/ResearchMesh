from copy import deepcopy
import json
from pathlib import Path
import unittest

from run_wu_aip import select_task, assess_outcome

ROOT = Path(__file__).resolve().parents[1]


class WuIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "examples/planner_cases.json").read_text(encoding="utf-8"))[0]["plan"]
        self.task, self.payload = select_task(self.plan)
        self.outcome = {"protocolCompleted": True, "businessReady": False, "error": None,
            "warnings": ["TRANSPORT_TEST_ONLY: no research was performed"],
            "result": {"schemaVersion": "0.1", "subtaskId": "S1", "taskType": "literature_search",
                       "summary": "Transport only", "items": [], "warnings": []}}
        self.events = [{"command": "start", "state": "awaiting-completion"},
                       {"command": "complete", "state": "completed"}]

    def test_only_business_fields_sent(self):
        self.assertEqual(len(self.payload), 7)
        self.assertNotIn("requiredCapability", self.payload)
        self.assertNotIn("dependsOn", self.payload)

    def test_reader_not_silently_converted(self):
        with self.assertRaisesRegex(ValueError, "ONLY_LITERATURE"):
            select_task(self.plan, "S2")

    def test_missing_task_rejected(self):
        with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
            select_task(self.plan, "S99")

    def test_unexecuted_dependencies_rejected(self):
        new = deepcopy(self.plan["tasks"][0])
        new["subtaskId"], new["dependsOn"] = "S4", ["S1"]
        self.plan["tasks"].append(new)
        with self.assertRaisesRegex(ValueError, "DEPENDENCIES"):
            select_task(self.plan, "S4")

    def test_transport_never_counted_as_research(self):
        assessment = assess_outcome(self.outcome, self.payload, self.events)
        self.assertTrue(assessment["localAcceptancePassed"])
        self.assertFalse(assessment["researchSucceeded"])
        self.assertFalse(assessment["researchExecuted"])

    def test_missing_complete_fails(self):
        self.assertFalse(assess_outcome(self.outcome, self.payload, self.events[:1])["localAcceptancePassed"])

    def test_real_empty_result_not_mislabeled_as_transport(self):
        self.outcome["warnings"] = []
        self.outcome["businessReady"] = True
        assessment = assess_outcome(self.outcome, self.payload, self.events)
        self.assertFalse(assessment["transportTestOnly"])
        self.assertFalse(assessment["localAcceptancePassed"])

    def test_mismatched_result_rejected(self):
        self.outcome["result"]["subtaskId"] = "WRONG"
        with self.assertRaises(ValueError):
            assess_outcome(self.outcome, self.payload, self.events)

    def test_protocol_error_fails(self):
        self.outcome["error"] = "INVALID_RESULT"
        self.assertFalse(assess_outcome(self.outcome, self.payload, self.events)["protocolVerified"])
