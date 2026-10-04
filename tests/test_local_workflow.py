import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest

from aggregator import render_report
from mock_agents import build_mock_agents, LiteratureMockAgent, PaperReaderMockAgent
from orchestrator import LocalOrchestrator, make_task_payload
from planner_contract import validate_plan
from resolver import AgentHandle, MockAgentResolver
from result_contract import validate_result
from run_demo import run_example


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "planner_cases.json"


def example_plan(number=0):
    return json.loads(EXAMPLES.read_text(encoding="utf-8"))[number]["plan"]


def execute(plan, agents=None, timeout=5.0):
    resolver = MockAgentResolver(build_mock_agents() if agents is None else agents)
    return asyncio.run(LocalOrchestrator(resolver, timeout).run_plan(plan))


class LocalWorkflowTests(unittest.TestCase):
    def test_full_workflow_and_dependencies(self):
        plan = example_plan()
        original = deepcopy(plan)
        run = execute(plan)
        self.assertEqual(len(run["results"]), 3)
        self.assertEqual(run["executionWaves"], [["S1"], ["S2", "S3"]])
        self.assertTrue(all(record["succeeded"] for record in run["executionRecords"]))
        paper_title = run["results"][0]["items"][0]["data"]["title"]
        self.assertEqual(run["results"][1]["items"][0]["data"]["source"]["title"], paper_title)
        self.assertEqual(run["results"][2]["items"][0]["data"]["relatedPaper"], paper_title)
        self.assertEqual(plan, original)
        self.assertTrue(run["demoOnly"])
        for task, result in zip(plan["tasks"], run["results"]):
            validate_result(result, task)

    def test_business_payload_excludes_internal_fields(self):
        task = example_plan()["tasks"][1]
        payload = make_task_payload(task)
        self.assertNotIn("requiredSkill", payload)
        self.assertNotIn("dependsOn", payload)
        payload["constraints"]["maxPapers"] = 100
        self.assertEqual(task["constraints"]["maxPapers"], 3)

    def test_dependency_context_is_passed_separately(self):
        class Reader(PaperReaderMockAgent):
            async def execute(self, payload, dependency_results):
                self.payload = payload
                self.dependencies = dependency_results
                return await super().execute(payload, dependency_results)
        reader = Reader()
        agents = [agent for agent in build_mock_agents() if "paper_read" not in agent.capabilities]
        agents.append(AgentHandle("replacement-reader", ("paper_read",), reader))
        run = execute(example_plan(), agents)
        self.assertIn("S1", reader.dependencies)
        self.assertNotIn("dependsOn", reader.payload)
        self.assertEqual(run["executionRecords"][1]["agentId"], "replacement-reader")

    def test_one_failure_preserves_independent_result(self):
        run = execute(example_plan(), build_mock_agents("S2"))
        self.assertEqual([result["subtaskId"] for result in run["results"]], ["S1", "S3"])
        self.assertEqual(run["executionRecords"][1]["error"]["code"], "EXECUTION_ERROR")
        self.assertTrue(run["executionRecords"][2]["succeeded"])

    def test_failed_upstream_skips_dependents(self):
        run = execute(example_plan(), build_mock_agents("S1"))
        self.assertEqual(run["results"], [])
        for record in run["executionRecords"][1:]:
            self.assertTrue(record["skipped"])
            self.assertEqual(record["error"]["code"], "DEPENDENCY_FAILED")
            self.assertIsNone(record["agentId"])

    def test_missing_capability_is_reported(self):
        agents = [agent for agent in build_mock_agents() if "paper_read" not in agent.capabilities]
        run = execute(example_plan(), agents)
        self.assertEqual(run["executionRecords"][1]["error"]["code"], "NO_MATCHING_AGENT")
        self.assertTrue(run["executionRecords"][2]["succeeded"])

    def test_clarification_does_not_execute(self):
        run = execute(example_plan(2), [])
        self.assertEqual(run["executionRecords"], [])
        self.assertEqual(run["results"], [])
        self.assertTrue(run["needsClarification"])
        self.assertIn("需要补充信息", render_report(run))

    def test_single_paper_read(self):
        run = execute(example_plan(1))
        self.assertEqual(len(run["results"]), 1)
        self.assertEqual(run["results"][0]["taskType"], "paper_read")

    def test_report_marks_mock_data_and_keeps_failures(self):
        report = render_report(execute(example_plan(), build_mock_agents("S2")))
        self.assertIn("模拟数据", report)
        self.assertIn("example.invalid", report)
        self.assertIn("EXECUTION_ERROR", report)
        self.assertIn("S3：repository_analyze", report)

    def test_invalid_result_is_not_accepted(self):
        class WrongResult(LiteratureMockAgent):
            async def execute(self, payload, dependency_results):
                result = await super().execute(payload, dependency_results)
                result["subtaskId"] = "WRONG"
                return result
        agents = [agent for agent in build_mock_agents() if "literature_search" not in agent.capabilities]
        agents.append(AgentHandle("bad-agent", ("literature_search",), WrongResult()))
        run = execute(example_plan(), agents)
        self.assertEqual(run["executionRecords"][0]["error"]["code"], "INVALID_RESULT")
        self.assertEqual(run["results"], [])

    def test_result_has_no_business_status(self):
        task = example_plan()["tasks"][0]
        result = asyncio.run(LiteratureMockAgent().execute(make_task_payload(task), {}))
        result["status"] = "completed"
        with self.assertRaises(ValueError):
            validate_result(result, task)

    def test_execution_timeout_is_isolated(self):
        class NeverCompletes:
            async def execute(self, payload, dependency_results):
                await asyncio.Event().wait()
        run = execute(example_plan(1), [AgentHandle("slow", ("paper_read",), NeverCompletes())], timeout=0.02)
        self.assertEqual(run["executionRecords"][0]["error"]["code"], "EXECUTION_TIMEOUT")

    def test_ready_tasks_really_run_concurrently(self):
        async def scenario():
            class Rendezvous(LiteratureMockAgent):
                def __init__(self):
                    self.count = 0
                    self.both_started = asyncio.Event()
                async def execute(self, payload, dependency_results):
                    self.count += 1
                    if self.count == 2:
                        self.both_started.set()
                    await self.both_started.wait()
                    return await super().execute(payload, dependency_results)
            plan = example_plan()
            second = deepcopy(plan["tasks"][0])
            second["subtaskId"] = "S2"
            plan["tasks"] = [plan["tasks"][0], second]
            executor = Rendezvous()
            resolver = MockAgentResolver([AgentHandle("parallel", ("literature_search",), executor)])
            return await LocalOrchestrator(resolver, 0.5).run_plan(plan)
        run = asyncio.run(scenario())
        self.assertEqual(len(run["results"]), 2)
        self.assertEqual(run["executionWaves"], [["S1", "S2"]])

    def test_malformed_task_type_raises_value_error(self):
        plan = example_plan()
        plan["tasks"][0]["taskType"] = []
        with self.assertRaises(ValueError):
            validate_plan(plan)

    def test_duplicate_task_id_is_rejected(self):
        plan = example_plan()
        plan["tasks"][1]["subtaskId"] = "S1"
        with self.assertRaises(ValueError):
            execute(plan)

    def test_demo_uses_existing_planner_and_labels_fixture(self):
        plan, run = asyncio.run(run_example(1))
        self.assertEqual(run["userQuery"], plan["userQuery"])
        self.assertEqual(run["plannerMode"], "fixture")


if __name__ == "__main__":
    unittest.main()
