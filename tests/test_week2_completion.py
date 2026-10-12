"""Meeting gap-closure tests: actual Provider/ADP/SDK, all external calls replaced."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import patch

import httpx
from openai import OpenAI
from acps_sdk.aip import AipRpcClient
from common.llm.researchmesh_llm import LLMProvider, LLMConfig
from aip_business_partner import create_local_business_app
from aip_executor import AipAgentExecutor
from aip_lifecycle import AipTaskRunner, sdk_sender
from aip_orchestrator import AipOrchestrator
from llm_provider import ProviderSettings
from planner import Planner, PlannerError
from resolver.adp import AdpAgentResolver, AdpResolverError
from resolver.base import NoMatchingAgentError
from run_aip_business import fixture_plan
from team_planning_model import TeamPlanningModel

ROOT = Path(__file__).resolve().parents[1]


def adp_fixture():
    return json.loads((ROOT / "examples/adp_offline.json").read_text(encoding="utf-8"))


def chain_discovery():
    data = adp_fixture(); acs = deepcopy(data["result"]["acsMap"]["offline-literature"])
    acs.update(aic="offline-reader", name="Offline Reader")
    acs["skills"][0].update(id="paper.analyze")
    data["result"]["acsMap"]["offline-reader"] = acs
    data["result"]["agents"][0]["agentSkills"].append({"aic": "offline-reader", "skillId": "paper.analyze", "ranking": 2})
    return data


async def synthetic_execute(task):
    result = {"schemaVersion": "0.2", "subtaskId": task["subtaskId"], "taskType": task["taskType"],
              "summary": "Offline synthetic result, not research", "items": [], "warnings": []}
    if task["taskType"] == "literature_search":
        data = {"paperId": "offline-p1", "title": "Offline paper", "doi": None,
                "url": "https://example.invalid/p1", "pdfUrl": "https://example.invalid/p1.pdf", "repositoryUrls": [],
                "year": 2025, "authors": ["Synthetic"], "relevance": "Synthetic test only"}
        result["items"] = [{"kind": "paper", "data": data}]
    else:
        paper = deepcopy(task["input"]["papers"][0])
        result["items"] = [{"kind": "paper_note", "data": {"source": paper, "analysisLevel": "fulltext",
            "method": "Synthetic method", "findings": "Synthetic finding", "limitations": "Synthetic limitation",
            "evidence": [{"sourceId": paper["paperId"], "url": paper["pdfUrl"], "locator": "Synthetic page 2",
                          "supports": "Synthetic evidence supports this conclusion."}]}}]
    return result


class AdpTests(unittest.IsolatedAsyncioTestCase):
    def resolver(self, data=None, **kwargs):
        async def send(request): return deepcopy(data if data is not None else adp_fixture())
        return AdpAgentResolver(send, lambda candidate: SimpleNamespace(execute=lambda *a: None), **kwargs)

    async def test_sdk_request_and_security_preserved(self):
        received = []
        async def send(request): received.append(request); return adp_fixture()
        candidates = []
        def factory(candidate): candidates.append(candidate); return SimpleNamespace(execute=lambda *a: None)
        resolver = AdpAgentResolver(send, factory)
        handle = await resolver.resolve("literature.search")
        self.assertEqual(handle.capabilities, ("literature.search",))
        self.assertEqual(received[0]["filter"]["conditions"][1], {"field": "skills.id", "op": "eq", "value": "literature.search"})
        self.assertEqual(candidates[0]["acs"]["endPoints"][0]["security"], [{"mtls": []}])
        self.assertNotIn("acs", resolver.records[0])

    async def test_explicit_skill_mapping(self):
        data = adp_fixture()
        data["result"]["acsMap"]["offline-literature"]["skills"][0]["id"] = "platform.literature.v1"
        data["result"]["agents"][0]["agentSkills"][0]["skillId"] = "platform.literature.v1"
        resolver = self.resolver(data, skill_map={"literature.search": "platform.literature.v1"})
        handle = await resolver.resolve("literature.search")
        self.assertEqual(handle.capabilities, ("literature.search",))
        self.assertEqual(resolver.records[0]["platformSkillId"], "platform.literature.v1")

    async def test_ranking_and_dedup(self):
        data = adp_fixture(); data["result"]["agents"][0]["agentSkills"] *= 2
        resolver = self.resolver(data); await resolver.resolve("literature.search")
        self.assertEqual(resolver.records[0]["candidateCount"], 1)

    async def test_inactive_mismatched_identity_skill_or_missing_acs_rejected(self):
        for kind in ("inactive", "identity", "skill", "missing"):
            data = adp_fixture(); acs = data["result"]["acsMap"]["offline-literature"]
            if kind == "inactive": acs["active"] = False
            elif kind == "identity": acs["aic"] = "other"
            elif kind == "skill": acs["skills"] = []
            else: data["result"]["acsMap"] = {}
            with self.subTest(kind=kind), self.assertRaises(NoMatchingAgentError):
                await self.resolver(data).resolve("literature.search")

    async def test_malformed_or_server_error_redacted(self):
        for data, code in (({}, "ADP_INVALID_RESPONSE"), ({"error": {"code": 403, "message": "private-secret"}}, "ADP_SERVER_ERROR")):
            resolver = self.resolver(data)
            with self.assertRaisesRegex(AdpResolverError, code): await resolver.resolve("literature.search")
            self.assertNotIn("private-secret", json.dumps(resolver.records))

    async def test_transport_error_and_timeout_redacted(self):
        async def broken(request): raise RuntimeError("private-secret")
        async def slow(request): await asyncio.sleep(1)
        for send, code in ((broken, "ADP_TRANSPORT_ERROR"), (slow, "ADP_TIMEOUT")):
            resolver = AdpAgentResolver(send, MockExecutor, timeout_seconds=.005)
            with self.assertRaisesRegex(AdpResolverError, code): await resolver.resolve("literature.search")
            self.assertNotIn("private-secret", json.dumps(resolver.records))

    async def test_unmapped_skill_does_not_send(self):
        async def never(request): raise AssertionError("must not send")
        resolver = AdpAgentResolver(never, MockExecutor)
        with self.assertRaisesRegex(AdpResolverError, "UNMAPPED"): await resolver.resolve("literature_search")

    async def test_missing_transport_configuration_fails_without_fallback(self):
        async def send(request): return adp_fixture()
        def broken(candidate): raise RuntimeError("private-secret")
        resolver = AdpAgentResolver(send, broken)
        with self.assertRaisesRegex(AdpResolverError, "ADP_EXECUTOR_CONFIGURATION_REQUIRED"):
            await resolver.resolve("literature.search")

    async def test_invalid_timeout_and_limit_rejected(self):
        for options in ({"timeout_seconds": True}, {"timeout_seconds": float("nan")}, {"limit": True}, {"limit": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError): self.resolver(**options)


class MockExecutor:
    def __init__(self, candidate): pass
    async def execute(self, *args): raise AssertionError("Not for live execution")


class TeamProviderTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, contents, *, status=200):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            if status != 200: return httpx.Response(status, json={"error": {"message": "private-secret"}})
            content = contents[min(len(calls)-1, len(contents)-1)]
            return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
                "model": "test", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})
        client = OpenAI(api_key="offline-not-real", base_url="https://example.invalid/v1", max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        provider = LLMProvider(LLMConfig("offline-not-real", "https://example.invalid/v1", "test", 1, 0), client=client)
        settings = ProviderSettings("offline-not-real", "openai-compatible", "https://example.invalid/v1", "test", 1)
        return provider, settings, calls

    async def test_actual_team_provider_and_planner_json_schema(self):
        plan = fixture_plan(); provider, settings, calls = self.provider([json.dumps(plan)])
        async with TeamPlanningModel(settings, provider=provider) as model:
            result = await Planner(model, timeout_seconds=1).plan(plan["userQuery"])
            self.assertEqual(result, plan); self.assertEqual(model.request_count, 1)
            self.assertEqual(model.last_usage["totalTokens"], 30)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_tokens"], settings.max_tokens)

    async def test_two_invalid_responses_bounded_two_calls(self):
        provider, settings, calls = self.provider(["not JSON"])
        async with TeamPlanningModel(settings, provider=provider) as model:
            with self.assertRaises(PlannerError): await Planner(model, timeout_seconds=1).plan("only search a topic")
        self.assertEqual(len(calls), 2)

    async def test_rate_limit_does_not_retry_or_leak(self):
        provider, settings, calls = self.provider([], status=429)
        async with TeamPlanningModel(settings, provider=provider) as model:
            with self.assertRaises(PlannerError) as error: await Planner(model, timeout_seconds=1).plan("only search a topic")
        self.assertEqual(len(calls), 1); self.assertNotIn("private-secret", str(error.exception))

    async def test_refuses_layered_retries(self):
        provider, settings, calls = self.provider(["{}"])
        provider.config = LLMConfig("offline-not-real", "https://example.invalid/v1", "test", 1, 2)
        with self.assertRaisesRegex(ValueError, "RETRIES_MUST_BE_ZERO"): TeamPlanningModel(settings, provider=provider)
        provider.client.close(); self.assertFalse(calls)

    async def test_cancelled_sync_request_never_sends_replacement(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def chat(*args, **kwargs):
            calls.append(True); entered.set(); release.wait(timeout=1)
            return SimpleNamespace(content="{}", usage=None)
        settings = ProviderSettings("offline", "openai-compatible", "https://example.invalid/v1", "test", 1)
        provider = SimpleNamespace(config=SimpleNamespace(max_retries=0), chat=chat)
        async with TeamPlanningModel(settings, provider=provider) as model:
            task = asyncio.create_task(model.generate("JSON", "topic"))
            await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            try:
                with self.assertRaisesRegex(Exception, "PREVIOUS_REQUEST_UNCERTAIN"):
                    await model.generate("JSON", "topic")
            finally: release.set()
        self.assertEqual(calls, [True])

    async def test_prompt_does_not_promise_unimplemented_paper_ranking(self):
        from planner import PLANNER_INSTRUCTIONS
        self.assertIn("不做智能代表性筛选", PLANNER_INSTRUCTIONS)


class AdpAipChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.clients = []
    async def asyncTearDown(self):
        for client in self.clients: await client.close()

    def resolver(self, *, failure=None):
        self.requests, self.payloads = [], []
        async def send(request): self.requests.append(request); return chain_discovery()
        def factory(candidate):
            aic = candidate["aic"]
            async def execute(payload):
                self.payloads.append(deepcopy(payload))
                if payload["subtaskId"] == failure: raise RuntimeError("private-secret")
                return await synthetic_execute(payload)
            app = create_local_business_app(execute=execute, partner_aic=aic, background_execution=True)
            client = AipRpcClient("http://127.0.0.1/rpc", "offline-leader", identity_binding_enabled=False,
                                  transport=httpx.ASGITransport(app=app))
            self.clients.append(client)
            return AipAgentExecutor(AipTaskRunner(sdk_sender(client), "offline-leader", aic, poll_seconds=.001),
                                    session_id="offline-chain-session")
        return AdpAgentResolver(send, factory)

    async def test_official_adp_resolver_aip_chain_binding_and_receipts(self):
        resolver = self.resolver()
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("external network")):
            result = await AipOrchestrator(resolver).run_plan(fixture_plan())
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["executionWaves"], [["S1"], ["S2"]])
        self.assertEqual(self.payloads[1]["input"]["papers"][0]["paperId"], "offline-p1")
        self.assertTrue(all(len(p) == 7 for p in self.payloads))
        self.assertTrue(all(r["aipState"] == "completed" and r["aipTaskId"] for r in result["executionRecords"]))
        self.assertEqual(len(result["executionRecords"][1]["evidence"]), 1)
        self.assertFalse(result["platformDiscoveryVerified"]); self.assertFalse(result["realResearchVerified"])

    async def test_upstream_failure_skips_reader_and_retains_error(self):
        result = await AipOrchestrator(self.resolver(failure="S1")).run_plan(fixture_plan())
        self.assertEqual(result["executionRecords"][0]["error"]["code"], "PARTNER_FAILED")
        self.assertEqual(result["executionRecords"][1]["error"]["code"], "DEPENDENCY_FAILED")
        self.assertEqual(len(self.payloads), 1); self.assertEqual(len(self.requests), 1)

    async def test_reader_failure_retains_search(self):
        result = await AipOrchestrator(self.resolver(failure="S2")).run_plan(fixture_plan())
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["executionRecords"][0]["succeeded"])
        self.assertIsNotNone(result["executionRecords"][1]["aipTaskId"])

    async def test_no_mock_execution_fallback(self):
        async def send(request): return adp_fixture()
        resolver = AdpAgentResolver(send, MockExecutor)
        result = await AipOrchestrator(resolver).run_plan(fixture_plan())
        self.assertFalse(result["results"])
        self.assertEqual(result["executionRecords"][0]["error"]["code"], "AIP_INTEGRATION_ERROR")
