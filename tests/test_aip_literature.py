"""Offline SDK + real Zhang Wrapper, fake Provider; never real API/Key/LLM."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import json

import httpx
from acps_sdk.aip import AipRpcClient, StructuredDataItem, TaskCommand, TaskCommandType, TaskState
from aip_business_partner import create_local_business_app, fixture_execute
from aip_literature_partner import make_literature_executor
from literature_adapter import AdapterError, load_wrapper
from run_aip_business import fixture_plan
from run_aip_literature import select_search_task, invoke_literature, assess_live_run, main

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "agents/literature"


class LiteratureAipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.wrapper = load_wrapper(WRAPPER)
        self.aic = "literature-test-" + str(uuid4())
        self.received, self.saved = [], []
        import importlib
        schemas = importlib.import_module(self.wrapper.__name__ + ".schemas")
        provider_module = importlib.import_module(self.wrapper.__name__ + ".provider")
        received = self.received
        class FakeProvider:
            async def search(self, request):
                received.append(request.model_dump())
                return provider_module.ProviderSearchResult([
                    schemas.PaperRecord(paperId="offline-wrapper-paper", title="【离线测试】Wrapper paper",
                                        year=2025, url="https://example.invalid/offline/paper",
                                        doi=None, pdfUrl=None, repositoryUrls=[])], 1, 1, False)
            def http_diagnostics(self):
                return {"httpObservationAvailable": False, "httpRequests": []}
        self.prepared = (SimpleNamespace(
            LiteratureAgent=self.wrapper.LiteratureAgent,
            LiteratureSearchRequest=self.wrapper.LiteratureSearchRequest,
            SemanticScholarProvider=lambda **kwargs: FakeProvider()), object())
        self.executor = make_literature_executor(WRAPPER, prepared=self.prepared,
                                                 on_outcome=self.saved.append)
        self.app = create_local_business_app(execute=self.executor, partner_aic=self.aic,
                                             background_execution=True, mode="local-literature-live")
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        # All normal tests poll to completion; cancel tests await cleanup below.

    def client(self, app=None):
        client = AipRpcClient("http://127.0.0.1/rpc", "local-literature-leader",
                              identity_binding_enabled=False, transport=httpx.ASGITransport(app=app or self.app))
        self.clients.append(client)
        return client

    async def rpc(self, client, kind, payload=None, *, task_id=None, session="offline-session"):
        command = TaskCommand(id=str(uuid4()), sentAt=datetime.now(timezone.utc).isoformat(),
                              senderRole="leader", senderId="local-literature-leader", sessionId=session,
                              taskId=task_id or str(uuid4()), command=TaskCommandType(kind),
                              dataItems=[StructuredDataItem(data=payload)] if payload is not None else None)
        return (await client._send_request(command)).result

    async def test_actual_wrapper_roundtrip_no_external_network(self):
        payload = select_search_task(fixture_plan())
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("external API")):
            outcome = await invoke_literature(payload, "http://127.0.0.1/rpc", self.aic,
                                              transport=httpx.ASGITransport(app=self.app), poll_seconds=0.001)
        self.assertIsNone(outcome["error"])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["schemaVersion"], "literature_search.v0.1")
        self.assertEqual(self.received[0]["query"], payload["input"]["query"])
        self.assertEqual(outcome["execution"]["result"]["schemaVersion"], "0.2")
        self.assertEqual(outcome["execution"]["result"]["items"][0]["data"]["paperId"], "offline-wrapper-paper")
        commands = [item["command"]["command"] for item in outcome["aipMessages"]]
        self.assertEqual(commands[0], "start")
        self.assertIn("get", commands)
        self.assertEqual(commands[-1], "complete")
        # The schema looks real, but no actual provider HTTP occurred: must not claim live PASS.
        acceptance = assess_live_run(outcome, self.saved[0])
        self.assertTrue(acceptance["protocolCompleted"])
        self.assertFalse(acceptance["passed"])
        self.assertFalse(acceptance["realSourceRequested"])

    async def test_background_returns_working_before_backend_finishes(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting(task):
            entered.set()
            await release.wait()
            return await self.executor(task)
        app = create_local_business_app(execute=waiting, partner_aic=self.aic,
                                        background_execution=True, mode="local-literature-live")
        client, task_id = self.client(app), str(uuid4())
        response = await asyncio.wait_for(self.rpc(client, "start", select_search_task(fixture_plan()), task_id=task_id), 0.2)
        self.assertEqual(response.status.state, TaskState.Working)
        await asyncio.wait_for(entered.wait(), 1)
        duplicate = await self.rpc(client, "start", select_search_task(fixture_plan()), task_id=task_id)
        self.assertEqual(duplicate.status.state, TaskState.Working)
        release.set()
        for _ in range(100):
            response = await self.rpc(client, "get", task_id=task_id)
            if response.status.state != TaskState.Working:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(response.status.state, TaskState.AwaitingCompletion)
        self.assertEqual(len(self.received), 1)
        response = await self.rpc(client, "complete", task_id=task_id)
        self.assertEqual(response.status.state, TaskState.Completed)

    async def test_background_cancel_requests_local_job_cancellation(self):
        entered, canceled = asyncio.Event(), asyncio.Event()
        async def waiting(task):
            entered.set()
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                canceled.set()
                raise
        app = create_local_business_app(execute=waiting, partner_aic=self.aic,
                                        background_execution=True, mode="local-literature-live")
        client, task_id = self.client(app), str(uuid4())
        await self.rpc(client, "start", select_search_task(fixture_plan()), task_id=task_id)
        await asyncio.wait_for(entered.wait(), 1)
        result = await self.rpc(client, "cancel", task_id=task_id)
        self.assertEqual(result.status.state, TaskState.Canceled)
        await asyncio.wait_for(canceled.wait(), 1)
        result = await self.rpc(client, "get", task_id=task_id)
        self.assertFalse(result.products)

    async def test_lifespan_cleanup_cancels_background_worker(self):
        entered, canceled = asyncio.Event(), asyncio.Event()
        async def waiting(task):
            entered.set()
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                canceled.set()
                raise
        app = create_local_business_app(execute=waiting, partner_aic=self.aic, background_execution=True)
        async with app.router.lifespan_context(app):
            await self.rpc(self.client(app), "start", select_search_task(fixture_plan()))
            await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(canceled.is_set())

    async def test_background_failure_polled_not_restarted(self):
        async def failure(task):
            raise RuntimeError("secret-from-fake-provider")
        app = create_local_business_app(execute=failure, partner_aic=self.aic,
                                        background_execution=True, mode="local-literature-live")
        outcome = await invoke_literature(select_search_task(fixture_plan()), "http://127.0.0.1/rpc", self.aic,
                                          transport=httpx.ASGITransport(app=app), poll_seconds=0.001)
        self.assertEqual(outcome["error"]["code"], "PARTNER_FAILED")
        self.assertNotIn("secret-from", json.dumps(outcome))
        self.assertEqual(sum(e["command"]["command"] == "start" for e in outcome["aipMessages"]), 1)

    async def test_live_mode_rejects_reader_before_callback(self):
        search = await fixture_execute(select_search_task(fixture_plan()))
        from input_binding import bind_task_input
        from common.models import BUSINESS_TASK_FIELDS
        task = bind_task_input(fixture_plan()["tasks"][1], {"S1": search})
        payload = {k: task[k] for k in BUSINESS_TASK_FIELDS}
        response = await self.rpc(self.client(), "start", payload)
        self.assertEqual(response.status.state, TaskState.Rejected)
        self.assertFalse(self.received)

    async def test_executor_no_result_preserves_empty_semantics(self):
        async def empty(*args, **kwargs):
            payload = args[0]
            return {"result": {"schemaVersion": "0.2", "subtaskId": payload["subtaskId"],
                               "taskType": "literature_search", "summary": "No matching papers",
                               "items": [], "warnings": ["NO_RESULTS"]}, "error": None,
                    "diagnostics": {"outcome": "empty"}}
        with patch("aip_literature_partner.execute_live_task", side_effect=empty):
            outcome = await invoke_literature(select_search_task(fixture_plan()), "http://127.0.0.1/rpc", self.aic,
                                              transport=httpx.ASGITransport(app=self.app), poll_seconds=0.001)
        self.assertIsNone(outcome["error"])
        acceptance = assess_live_run(outcome, self.saved[0])
        self.assertTrue(acceptance["emptyResult"])
        self.assertFalse(acceptance["businessReady"])

    async def test_executor_rate_limit_propagates_safe_error(self):
        async def limited(*args, **kwargs):
            return {"result": None, "error": {"code": "RATE_LIMITED", "retryable": True},
                    "diagnostics": {"rateLimitRecovery": {"exhausted": True}}}
        with patch("aip_literature_partner.execute_live_task", side_effect=limited):
            outcome = await invoke_literature(select_search_task(fixture_plan()), "http://127.0.0.1/rpc", self.aic,
                                              transport=httpx.ASGITransport(app=self.app), poll_seconds=0.001)
        self.assertEqual(outcome["error"]["code"], "PARTNER_FAILED")
        self.assertEqual(self.saved[0]["error"]["code"], "RATE_LIMITED")
        self.assertFalse(assess_live_run(outcome, self.saved[0])["passed"])

    async def test_adapter_failure_saved_without_raw_exception(self):
        async def invalid(*args, **kwargs):
            raise AdapterError("WRAPPER_CALL_FAILED")
        with patch("aip_literature_partner.execute_live_task", side_effect=invalid):
            outcome = await invoke_literature(select_search_task(fixture_plan()), "http://127.0.0.1/rpc", self.aic,
                                              transport=httpx.ASGITransport(app=self.app), poll_seconds=0.001)
        self.assertEqual(self.saved[0]["error"]["code"], "WRAPPER_CALL_FAILED")
        self.assertIsNone(outcome["execution"])

    async def test_select_only_s1_preserves_whole_plan(self):
        plan = fixture_plan()
        before = deepcopy(plan)
        payload = select_search_task(plan)
        self.assertEqual(plan, before)
        self.assertEqual(payload["schemaVersion"], "0.2")
        self.assertNotIn("requiredSkill", payload)
        self.assertNotIn("dependsOn", payload)
        self.assertEqual(len(payload), 7)

    async def test_select_other_or_missing_tasks_rejected(self):
        for task_id in ("S2", "S99"):
            with self.assertRaises(AdapterError):
                select_search_task(fixture_plan(), task_id)

    async def test_invalid_legacy_plan_rejected(self):
        plan = fixture_plan()
        plan["schemaVersion"] = "0.1"
        with self.assertRaises(ValueError):
            select_search_task(plan)

    async def test_rpc_rejects_nonloopback_or_credentials(self):
        for url in ("https://api.example.invalid/rpc", "http://key:secret@127.0.0.1/rpc"):
            with self.assertRaises(AdapterError):
                await invoke_literature(select_search_task(fixture_plan()), url, self.aic)

    async def test_health_indicates_config_not_research_success(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://local") as client:
            health = (await client.get("/health")).json()
        self.assertEqual(health["skills"], ["literature.search"])
        self.assertTrue(health["backgroundExecution"])
        self.assertNotIn("businessReady", health)

    async def test_leader_timeout_does_not_repeat_start(self):
        entered = asyncio.Event()
        async def slow(task):
            entered.set()
            await asyncio.sleep(100)
        app = create_local_business_app(execute=slow, partner_aic=self.aic, background_execution=True)
        async with app.router.lifespan_context(app):
            outcome = await invoke_literature(select_search_task(fixture_plan()), "http://127.0.0.1/rpc", self.aic,
                                              transport=httpx.ASGITransport(app=app), timeout_seconds=0.015, poll_seconds=0.001)
            self.assertEqual(outcome["error"]["code"], "AIP_TIMEOUT_REMOTE_STATE_UNKNOWN")
            self.assertTrue(outcome["trace"]["remoteStateUnknown"])
            self.assertEqual(sum(e["command"]["command"] == "start" for e in outcome["aipMessages"]), 1)


class AipLiteratureEvidenceTests(unittest.TestCase):
    def artifacts(self, empty=False):
        result = {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
                  "summary": "offline test", "items": [] if empty else [{"kind": "paper", "data": {}}], "warnings": []}
        outcome = {"execution": {"state": "completed", "result": result, "simulatedResult": False}, "error": None}
        backend = {"result": deepcopy(result), "error": None,
                   "diagnostics": {"httpObservationAvailable": True, "httpRequests": [{"httpStatus": 200}]}}
        return outcome, backend

    def test_matching_protocol_and_observation_required(self):
        outcome, backend = self.artifacts()
        self.assertTrue(assess_live_run(outcome, backend)["passed"])
        backend["result"]["summary"] = "tampered"
        self.assertFalse(assess_live_run(outcome, backend)["passed"])

    def test_no_http_observation_no_live_claim(self):
        outcome, backend = self.artifacts()
        backend["diagnostics"]["httpRequests"] = []
        self.assertFalse(assess_live_run(outcome, backend)["businessReady"])

    def test_empty_result_not_business_ready(self):
        outcome, backend = self.artifacts(empty=True)
        check = assess_live_run(outcome, backend)
        self.assertTrue(check["passed"])
        self.assertFalse(check["businessReady"])
        self.assertTrue(check["emptyResult"])

    def test_failed_protocol_not_business_ready(self):
        outcome, backend = self.artifacts()
        outcome.update(execution=None, error={"code": "PARTNER_FAILED"})
        self.assertFalse(assess_live_run(outcome, backend)["passed"])

    def test_mock_result_never_live_pass(self):
        outcome, backend = self.artifacts()
        outcome["execution"]["simulatedResult"] = True
        self.assertFalse(assess_live_run(outcome, backend)["passed"])

    def test_default_cli_preflight_no_partner_or_network(self):
        import run_aip_literature as cli
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.json"
            plan.write_text(json.dumps(fixture_plan()), encoding="utf-8")
            with patch.object(cli, "ROOT", root), patch.object(cli, "prepare_live_wrapper", return_value=(object(), object())), \
                 patch.object(cli, "live_partner", side_effect=AssertionError("started partner without --execute")), \
                 patch.object(httpx.Client, "get", side_effect=AssertionError("network in preflight")):
                code = main(["--plan", str(plan), "--wrapper-root", str(WRAPPER)])
            self.assertEqual(code, 0)
            record = next((root / "outputs").glob("*/preflight.json"))
            self.assertFalse(json.loads(record.read_text(encoding="utf-8"))["networkAttempted"])

    def test_missing_key_preflight_safe_error(self):
        import run_aip_literature as cli
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.json"
            plan.write_text(json.dumps(fixture_plan()), encoding="utf-8")
            with patch.object(cli, "ROOT", root), patch.object(cli, "prepare_live_wrapper",
                 side_effect=AdapterError("MISSING_SEMANTIC_SCHOLAR_API_KEY")):
                code = main(["--plan", str(plan)])
            self.assertEqual(code, 1)
            record = next((root / "outputs").glob("*/failure.json"))
            self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["error"], "MISSING_SEMANTIC_SCHOLAR_API_KEY")
