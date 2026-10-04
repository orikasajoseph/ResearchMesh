import asyncio
import json
import unittest
from pathlib import Path

from planner import Planner, PlannerError


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "planner_cases.json"


class StaticModel:
    def __init__(self, answer):
        self.answer = answer
        self.instructions = None

    async def generate(self, instructions, user_query):
        self.instructions = instructions
        return self.answer


class PlannerTests(unittest.TestCase):
    def test_invalid_first_response_is_retried_once(self):
        answer = json.loads(EXAMPLES.read_text(encoding="utf-8"))[0]["plan"]

        class RepairModel:
            def __init__(self):
                self.calls = []

            async def generate(self, instructions, user_query):
                self.calls.append(instructions)
                return "bad JSON" if len(self.calls) == 1 else json.dumps(answer)

        model = RepairModel()
        result = asyncio.run(Planner(model).plan(answer["userQuery"]))
        self.assertEqual(result, answer)
        self.assertEqual(len(model.calls), 2)
        self.assertIn("上次输出", model.calls[1])

    def test_retry_exhaustion_is_not_replaced_with_a_fake_plan(self):
        class BadModel:
            calls = 0

            async def generate(self, instructions, user_query):
                self.calls += 1
                return "not JSON"

        model = BadModel()
        with self.assertRaises(PlannerError):
            asyncio.run(Planner(model).plan("研究主题"))
        self.assertEqual(model.calls, 2)

    def test_schema_error_is_retried(self):
        answer = json.loads(EXAMPLES.read_text(encoding="utf-8"))[0]["plan"]

        class SchemaRepairModel:
            calls = 0

            async def generate(self, instructions, user_query):
                self.calls += 1
                invalid = dict(answer, needsClarification="false")
                return json.dumps(invalid if self.calls == 1 else answer)

        model = SchemaRepairModel()
        self.assertEqual(asyncio.run(Planner(model).plan(answer["userQuery"])), answer)
        self.assertEqual(model.calls, 2)

    def test_timeout_is_reported(self):
        class SlowModel:
            async def generate(self, instructions, user_query):
                await asyncio.sleep(1)
                return "{}"

        with self.assertRaisesRegex(PlannerError, "超时"):
            asyncio.run(Planner(SlowModel(), timeout_seconds=0.01).plan("研究主题"))

    def test_provider_error_is_not_exposed(self):
        class FailingModel:
            async def generate(self, instructions, user_query):
                raise RuntimeError("private-provider-detail")

        with self.assertRaises(PlannerError) as caught:
            asyncio.run(Planner(FailingModel()).plan("研究主题"))
        self.assertNotIn("private-provider-detail", str(caught.exception))

    def test_empty_and_long_input_is_rejected_before_calling_model(self):
        for query in ("", " ", None, 123, "字" * 6001):
            with self.assertRaises(ValueError):
                asyncio.run(Planner(StaticModel("{}")).plan(query))

    def test_invalid_configuration_is_rejected(self):
        for attempts in (0, 3, True):
            with self.assertRaises(ValueError):
                Planner(StaticModel("{}"), max_attempts=attempts)
        with self.assertRaises(ValueError):
            Planner(StaticModel("{}"), timeout_seconds=0)

    def test_examples_pass_through_the_planner(self):
        cases = json.loads(EXAMPLES.read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["name"]):
                plan = case["plan"]
                model = StaticModel(json.dumps(plan, ensure_ascii=False))
                result = asyncio.run(Planner(model).plan(plan["userQuery"]))
                self.assertEqual(result, plan)
                self.assertIn("Leader 内部", model.instructions)

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(PlannerError):
            asyncio.run(Planner(StaticModel("不是 JSON")).plan("调研图神经网络"))

    def test_mismatched_query_is_rejected(self):
        cases = json.loads(EXAMPLES.read_text(encoding="utf-8"))
        answer = json.dumps(cases[0]["plan"], ensure_ascii=False)
        with self.assertRaises(PlannerError):
            asyncio.run(Planner(StaticModel(answer)).plan("另一个研究问题"))


if __name__ == "__main__":
    unittest.main()
