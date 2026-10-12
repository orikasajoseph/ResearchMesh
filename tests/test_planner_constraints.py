"""Planner 约束白名单回归；仅用固定响应，不调用模型或科研 API。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from planner import Planner, PlannerError, PLANNER_INSTRUCTIONS
from planner_contract import ALLOWED_CONSTRAINTS, UnsupportedConstraintError, validate_plan
from run_research import load_resume_plan, load_resume_record


def fixture():
    path = Path(__file__).resolve().parents[1] / "examples" / "planner_cases.json"
    return deepcopy(json.loads(path.read_text(encoding="utf-8"))[0]["plan"])


class ConstraintTests(unittest.TestCase):
    def test_existing_examples_remain_valid(self):
        validate_plan(fixture())

    def test_reader_extra_quote_requirement_rejected_without_mutation(self):
        plan = fixture()
        plan["tasks"][1]["goal"] += "，并保留原文证据（引用原文片段）"
        plan["tasks"][1]["constraints"]["requireEvidenceQuotes"] = True
        before = deepcopy(plan)
        with self.assertRaisesRegex(UnsupportedConstraintError, "保留证据"):
            validate_plan(plan)
        self.assertEqual(plan, before)

    def test_unknown_constraints_rejected_for_every_task_type(self):
        for index in range(3):
            plan = fixture()
            plan["tasks"][index]["constraints"]["unknown"] = True
            with self.subTest(kind=plan["tasks"][index]["taskType"]), self.assertRaises(UnsupportedConstraintError):
                validate_plan(plan)

    def test_constraints_from_other_task_types_rejected(self):
        for index, key in [(0, "maxPapers"), (1, "yearFrom"), (2, "maxResults")]:
            plan = fixture()
            plan["tasks"][index]["constraints"][key] = 2025 if key == "yearFrom" else 1
            with self.subTest(key=key), self.assertRaises(UnsupportedConstraintError):
                validate_plan(plan)

    def test_declared_supported_constraints_remain_valid(self):
        plan = fixture()
        plan["tasks"][0]["constraints"] = {"yearFrom": 2017, "yearTo": 2018, "maxResults": 3}
        plan["tasks"][1]["constraints"] = {"maxPapers": 1}
        plan["tasks"][2]["constraints"] = {"maxRepositories": 1, "executeCode": False,
            "allowRepositorySearch": False, "requirePaperNotes": False}
        validate_plan(plan)
        self.assertEqual(ALLOWED_CONSTRAINTS["paper_read"], {"maxPapers"})

    def test_model_constraint_error_retries_once_preserving_goal(self):
        good = fixture()
        good["tasks"][1]["goal"] += "，并保留原文证据"
        bad = deepcopy(good)
        bad["tasks"][1]["constraints"]["requireEvidenceQuotes"] = True

        class RepairModel:
            def __init__(self): self.instructions = []
            async def generate(self, instructions, query):
                self.instructions.append(instructions)
                return json.dumps(bad if len(self.instructions) == 1 else good)
        model = RepairModel()
        result = asyncio.run(Planner(model).plan(good["userQuery"]))
        self.assertEqual(result, good)
        self.assertEqual(len(model.instructions), 2)
        self.assertIn("constraints 白名单", model.instructions[1])
        self.assertIn("goal", PLANNER_INSTRUCTIONS)

    def test_repeated_unknown_constraint_not_silently_discarded(self):
        bad = fixture()
        bad["tasks"][1]["constraints"]["requireEvidenceQuotes"] = True
        class BadModel:
            calls = 0
            async def generate(self, instructions, query):
                self.calls += 1
                return json.dumps(bad)
        model = BadModel()
        with self.assertRaises(PlannerError):
            asyncio.run(Planner(model).plan(bad["userQuery"]))
        self.assertEqual(model.calls, 2)
        self.assertTrue(bad["tasks"][1]["constraints"]["requireEvidenceQuotes"])

    def test_corrected_plan_only_directory_accepted_for_resume(self):
        plan = fixture()
        plan["tasks"] = plan["tasks"][:2]
        plan["tasks"][1]["constraints"] = {"maxPapers": 1}
        plan["tasks"][1]["goal"] += "，并保留原文证据（引用原文片段）"
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(load_resume_plan(directory, with_reader=True), plan)
            self.assertIsNone(load_resume_record(directory, plan))


if __name__ == "__main__":
    unittest.main()
