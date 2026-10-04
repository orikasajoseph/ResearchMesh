import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from input_binding import bind_task_input, InputBindingError
from llm_provider import ProviderSettings, ProviderPlanningModel
from deepseek_model import DeepSeekConfigError
from planner_contract import validate_plan
from mock_agents import LiteratureMockAgent, build_mock_agents
from orchestrator import LocalOrchestrator
from resolver import AgentHandle, MockAgentResolver
from live_api import create_app
from result_contract import validate_result

ROOT = Path(__file__).resolve().parents[1]


def plan():
    return json.loads((ROOT / "examples/planner_cases.json").read_text(encoding="utf-8"))[0]["plan"]


def result():
    return asyncio.run(LiteratureMockAgent().execute(plan()["tasks"][0], {}))


class Week2BindingTests(unittest.TestCase):
    def test_result_wrong_kind_rejected(self):
        value = result()
        value["items"][0]["kind"] = "repository"
        with self.assertRaises(ValueError):
            validate_result(value, plan()["tasks"][0])

    def test_result_missing_locator_rejected(self):
        value = result()
        value["items"][0]["data"]["url"] = None
        with self.assertRaises(ValueError):
            validate_result(value, plan()["tasks"][0])

    def test_non_mock_reading_needs_evidence_and_level(self):
        task = plan()["tasks"][1]
        value = {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
                 "summary": "analysis", "warnings": [], "items": [{"kind": "paper_note", "data": {
                     "source": {"url": "https://example.invalid/paper"}, "method": "method",
                     "findings": "findings", "limitations": "unknown"}}]}
        with self.assertRaises(ValueError):
            validate_result(value, task)
        data = value["items"][0]["data"]
        data["analysisLevel"] = "abstract"
        with self.assertRaises(ValueError):
            validate_result(value, task)
        data["evidence"] = [{"url": "https://example.invalid/paper", "sourceId": "P1", "locator": "abstract", "supports": "method"}]
        validate_result(value, task)

    def test_binding_ignores_undeclared_dependencies(self):
        task = plan()["tasks"][1]
        with self.assertRaises(InputBindingError):
            bind_task_input(task, {"OTHER": result()})

    def test_explicit_papers_take_priority(self):
        task = plan()["tasks"][1]
        task["input"]["papers"] = [{"doi": "10.1234/specific"}]
        bound = bind_task_input(task, {"S1": result()})
        self.assertEqual(bound["input"]["papers"][0]["doi"], "10.1234/specific")

    def test_actual_papers_bound_and_original_unchanged(self):
        task = plan()["tasks"][1]
        original = deepcopy(task)
        bound = bind_task_input(task, {"S1": result()})
        self.assertEqual(task, original)
        self.assertEqual(len(bound["input"]["papers"]), 1)
        self.assertTrue(bound["input"]["papers"][0]["paperId"])
        self.assertIn("example.invalid", bound["input"]["papers"][0]["url"])

    def test_empty_search_stops_reader(self):
        data = result()
        data["items"] = []
        with self.assertRaises(InputBindingError):
            bind_task_input(plan()["tasks"][1], {"S1": data})

    def test_missing_paper_locator_rejected(self):
        data = result()
        data["items"][0]["data"] = {"title": "Only a title"}
        with self.assertRaises(InputBindingError):
            bind_task_input(plan()["tasks"][1], {"S1": data})

    def test_new_plans_reject_legacy_skill(self):
        value = plan()
        value["tasks"][0]["requiredSkill"] = "literature_search"
        with self.assertRaises(ValueError):
            validate_plan(value)

    def test_no_string_placeholder(self):
        value = plan()
        value["tasks"][1]["input"] = {"papers": "S1 的输出"}
        with self.assertRaises(ValueError):
            validate_plan(value)

    def test_wrong_output_schema_rejected(self):
        value = plan()
        value["tasks"][0]["outputSchema"] = "code_records.v1"
        with self.assertRaises(ValueError):
            validate_plan(value)

    def test_reproduction_requires_reader_dependency(self):
        value = plan()
        value["tasks"][2]["constraints"]["requirePaperNotes"] = True
        with self.assertRaises(ValueError):
            validate_plan(value)

    def test_static_only(self):
        value = plan()
        value["tasks"][2]["constraints"]["executeCode"] = True
        with self.assertRaises(ValueError):
            validate_plan(value)

    def test_sequential_notes_reach_code(self):
        value = plan()
        value["tasks"][2]["dependsOn"] = ["S1", "S2"]
        value["tasks"][2]["constraints"]["requirePaperNotes"] = True
        run = asyncio.run(LocalOrchestrator(MockAgentResolver(build_mock_agents())).run_plan(value))
        self.assertEqual(run["executionWaves"], [["S1"], ["S2"], ["S3"]])
        code = run["executionRecords"][2]
        self.assertTrue(code["succeeded"])
        self.assertEqual(len(code["boundInput"]["paperNotes"]), 1)
        self.assertIsNone(code["aipTaskId"])

    def test_code_can_depend_only_on_reader_with_source(self):
        value = plan()
        value["tasks"][2]["dependsOn"] = ["S2"]
        value["tasks"][2]["constraints"]["requirePaperNotes"] = True
        run = asyncio.run(LocalOrchestrator(MockAgentResolver(build_mock_agents())).run_plan(value))
        bound = run["executionRecords"][2]
        self.assertTrue(bound["succeeded"])
        self.assertEqual(len(bound["boundInput"]["papers"]), 1)
        self.assertEqual(len(bound["boundInput"]["paperNotes"]), 1)

    def test_empty_results_not_mock_filled(self):
        class Empty(LiteratureMockAgent):
            async def execute(self, payload, dependency_results):
                value = await super().execute(payload, dependency_results)
                value["items"] = []
                return value
        agents = [a for a in build_mock_agents() if "literature_search" not in a.capabilities]
        agents.append(AgentHandle("empty", ("literature.search",), Empty()))
        run = asyncio.run(LocalOrchestrator(MockAgentResolver(agents)).run_plan(plan()))
        self.assertTrue(run["executionRecords"][0]["succeeded"])
        self.assertEqual(run["executionRecords"][1]["error"]["code"], "INPUT_INSUFFICIENT")

    def test_bad_limits_and_years(self):
        for fields in ({"maxPapers": 0}, {"maxResults": True}, {"yearFrom": 2026, "yearTo": 2023}):
            value = plan()
            value["tasks"][0]["constraints"] = fields
            with self.assertRaises(ValueError):
                validate_plan(value)


class ProviderTests(unittest.TestCase):
    def settings(self, env):
        return ProviderSettings.from_env(ROOT / "does-not-exist.env", environ=env)

    def test_existing_deepseek_config_reused(self):
        settings = self.settings({"DEEPSEEK_API_KEY": "test-secret"})
        self.assertEqual(settings.provider, "deepseek")
        self.assertEqual(settings.api_key, "test-secret")
        self.assertNotIn("test-secret", repr(settings))

    def test_other_provider_never_reuses_deepseek_key(self):
        with self.assertRaises(DeepSeekConfigError):
            self.settings({"LLM_PROVIDER": "qwen", "DEEPSEEK_API_KEY": "secret",
                           "LLM_BASE_URL": "https://example.invalid/v1", "LLM_MODEL": "test"})

    def test_deepseek_key_not_sent_to_custom_host(self):
        with self.assertRaises(DeepSeekConfigError):
            self.settings({"DEEPSEEK_API_KEY": "secret", "LLM_BASE_URL": "https://example.invalid/v1"})

    def test_generic_request_has_no_deepseek_thinking(self):
        from openai import AsyncOpenAI
        async def scenario():
            def handler(request):
                body = json.loads(request.content)
                self.assertNotIn("thinking", body)
                self.assertEqual(body["response_format"], {"type": "json_object"})
                return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
                    "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "{}"}}]})
            settings = ProviderSettings("test", "openai-compatible", "https://example.invalid/v1", "test")
            client = AsyncOpenAI(api_key="test", base_url=settings.base_url,
                                 http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            async with ProviderPlanningModel(settings, client=client) as model:
                await model.generate("JSON", "test")
        asyncio.run(scenario())

    def test_live_http_accepts_new_question_with_injected_model(self):
        async def scenario():
            async def fake(query):
                value = plan()
                value["userQuery"] = query
                return value, {"testOnly": True, "aipUsed": False}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test") as client:
                response = await client.post("/plan", json={"userQuery": "a genuinely new input"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["plan"]["userQuery"], "a genuinely new input")
                self.assertFalse((await client.get("/health")).json()["modelConnectionChecked"])
        asyncio.run(scenario())
