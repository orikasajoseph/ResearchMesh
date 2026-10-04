import json
import unittest
from pathlib import Path

from planner_contract import validate_plan


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "planner_cases.json"


class PlannerContractTests(unittest.TestCase):
    def test_three_examples_are_valid(self):
        cases = json.loads(EXAMPLES.read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(case=case["name"]):
                validate_plan(case["plan"])

    def test_unknown_task_type_is_rejected(self):
        cases = json.loads(EXAMPLES.read_text(encoding="utf-8"))
        plan = cases[1]["plan"]
        plan["tasks"][0]["taskType"] = "invented_task"
        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_unresolved_dependency_is_rejected(self):
        cases = json.loads(EXAMPLES.read_text(encoding="utf-8"))
        plan = cases[0]["plan"]
        plan["tasks"][1]["dependsOn"] = ["S999"]
        with self.assertRaises(ValueError):
            validate_plan(plan)


if __name__ == "__main__":
    unittest.main()
