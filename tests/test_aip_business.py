"""Official SDK client/server + ASGI memory HTTP, synthetic backend only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

import httpx
from acps_sdk.aip import AipRpcClient, StructuredDataItem, TaskCommand, TaskCommandType, TaskState
from aip_business_partner import create_local_business_app, fixture_execute, LocalBackendFailure
from aip_lifecycle import AipTaskRunner, AipExecutionError, sdk_sender
from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input
from run_aip_business import fixture_plan, run_fixture_chain


def search_task():
    task = fixture_plan()["tasks"][0]
    return {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}


class AipBusinessTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.aic = "test-partner-" + str(uuid4())
        self.calls = []
        async def execute(task):
            self.calls.append(deepcopy(task))
            return await fixture_execute(task)
        self.execute = execute
        self.app = create_local_business_app(execute=execute, partner_aic=self.aic)
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()

    def client(self, app=None):
        client = AipRpcClient("http://127.0.0.1/rpc", "test-leader",
                              transport=httpx.ASGITransport(app=app or self.app), identity_binding_enabled=False)
        self.clients.append(client)
        return client

    async def rpc(self, client, kind, payload=None, *, task_id=None, session="test-session", sender="test-leader"):
        command = TaskCommand(id=str(uuid4()), sentAt=datetime.now(timezone.utc).isoformat(),
                              senderId=sender, senderRole="leader", sessionId=session,
                              taskId=task_id or str(uuid4()), command=TaskCommandType(kind),
                              dataItems=[StructuredDataItem(data=payload)] if payload is not None else None)
        return (await client._send_request(command)).result

    async def test_complete_v02_chain_and_payloads(self):
        record = await run_fixture_chain("http://127.0.0.1/rpc", self.aic,
                                         transport=httpx.ASGITransport(app=self.app))
        self.assertTrue(record["acceptance"]["passed"])
        self.assertEqual([c["taskType"] for c in self.calls], ["literature_search", "paper_read"])
        self.assertEqual([e["state"] for e in record["executions"]], ["completed", "completed"])
        self.assertFalse(record["acceptance"]["businessReady"])
        self.assertFalse(record["acceptance"]["realResearchExecuted"])
        for payload in record["payloads"]:
            self.assertEqual(set(payload), set(BUSINESS_TASK_FIELDS))
            self.assertEqual(payload["schemaVersion"], "0.2")
        for exchange in record["aipMessages"]:
            self.assertEqual(exchange["response"]["jsonrpc"], "2.0")
        self.assertTrue(all(e["simulatedResult"] for e in record["executions"]))

    async def test_default_research_runner_rejects_mock_without_complete(self):
        client = self.client()
        runner = AipTaskRunner(sdk_sender(client), "test-leader", self.aic)
        with self.assertRaisesRegex(AipExecutionError, "INVALID_BUSINESS_RESULT") as caught:
            await runner.run(search_task(), "default-reject-mock")
        self.assertEqual([e["command"] for e in caught.exception.trace["events"]], ["start"])

    async def test_mock_warning_even_without_is_mock_still_rejected(self):
        async def execute(task):
            result = await fixture_execute(task)
            del result["items"][0]["data"]["isMock"]
            return result
        app = create_local_business_app(execute=execute, partner_aic=self.aic)
        runner = AipTaskRunner(sdk_sender(self.client(app)), "test-leader", self.aic)
        with self.assertRaises(AipExecutionError):
            await runner.run(search_task(), "reject-warning")

    async def test_working_state_recorded_in_sdk_history(self):
        result = await self.rpc(self.client(), "start", search_task())
        self.assertEqual([s.state.value for s in result.statusHistory], ["working", "awaiting-completion"])

    async def test_duplicate_start_executes_once(self):
        client, task_id = self.client(), str(uuid4())
        await self.rpc(client, "start", search_task(), task_id=task_id)
        response = await self.rpc(client, "start", search_task(), task_id=task_id)
        self.assertEqual(response.status.state, TaskState.AwaitingCompletion)
        self.assertEqual(len(self.calls), 1)

    async def test_duplicate_start_changed_payload_rejected_without_overwrite(self):
        client, task_id = self.client(), str(uuid4())
        original = await self.rpc(client, "start", search_task(), task_id=task_id)
        changed = search_task()
        changed["goal"] = "different goal"
        response = await self.rpc(client, "start", changed, task_id=task_id)
        self.assertEqual(response.status.state, TaskState.Rejected)
        saved = await self.rpc(client, "get", task_id=task_id)
        self.assertEqual(saved.products, original.products)
        self.assertEqual(len(self.calls), 1)

    async def test_cross_session_read_and_complete_rejected(self):
        client, task_id = self.client(), str(uuid4())
        await self.rpc(client, "start", search_task(), task_id=task_id)
        for kind in ("get", "complete", "cancel", "start"):
            response = await self.rpc(client, kind, search_task() if kind == "start" else None,
                                      task_id=task_id, session="other-session")
            self.assertEqual(response.status.state, TaskState.Rejected)
            self.assertFalse(response.products)
        response = await self.rpc(client, "get", task_id=task_id)
        self.assertEqual(response.status.state, TaskState.AwaitingCompletion)

    async def test_cross_sender_rejected(self):
        client, task_id = self.client(), str(uuid4())
        await self.rpc(client, "start", search_task(), task_id=task_id)
        response = await self.rpc(client, "get", task_id=task_id, sender="another-leader")
        self.assertEqual(response.status.state, TaskState.Rejected)

    async def test_legacy_v01_rejected_not_silently_converted(self):
        payload = search_task()
        payload["schemaVersion"] = "0.1"
        response = await self.rpc(self.client(), "start", payload)
        self.assertEqual(response.status.state, TaskState.Rejected)
        self.assertFalse(self.calls)

    async def test_internal_planner_fields_rejected(self):
        response = await self.rpc(self.client(), "start", fixture_plan()["tasks"][0])
        self.assertEqual(response.status.state, TaskState.Rejected)
        self.assertFalse(self.calls)

    async def test_unknown_constraints_rejected_before_backend(self):
        payload = search_task()
        payload["constraints"]["invented"] = True
        response = await self.rpc(self.client(), "start", payload)
        self.assertEqual(response.status.state, TaskState.Rejected)
        self.assertFalse(self.calls)

    async def test_output_schema_mismatch_rejected(self):
        payload = search_task()
        payload["outputSchema"] = "paper_notes.v1"
        response = await self.rpc(self.client(), "start", payload)
        self.assertEqual(response.status.state, TaskState.Rejected)

    async def test_reader_unbound_rejected_before_backend(self):
        task = fixture_plan()["tasks"][1]
        payload = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
        response = await self.rpc(self.client(), "start", payload)
        self.assertEqual(response.status.state, TaskState.Rejected)
        self.assertFalse(self.calls)

    async def test_reader_must_be_single_paper(self):
        result = await fixture_execute(search_task())
        task = bind_task_input(fixture_plan()["tasks"][1], {"S1": result})
        payload = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
        payload["constraints"]["maxPapers"] = 2
        response = await self.rpc(self.client(), "start", payload)
        self.assertEqual(response.status.state, TaskState.Rejected)
        self.assertFalse(self.calls)

    async def test_backend_failure_safe_standard_state(self):
        async def failure(task):
            raise LocalBackendFailure("RATE_LIMITED")
        app = create_local_business_app(execute=failure, partner_aic=self.aic)
        response = await self.rpc(self.client(app), "start", search_task())
        self.assertEqual(response.status.state, TaskState.Failed)
        self.assertEqual(response.status.dataItems[0].text, "RATE_LIMITED")
        self.assertFalse(response.products)

    async def test_backend_unknown_error_not_exposed(self):
        async def failure(task):
            raise RuntimeError("secret-fake-key-that-must-not-be-exposed")
        app = create_local_business_app(execute=failure, partner_aic=self.aic)
        response = await self.rpc(self.client(app), "start", search_task())
        self.assertEqual(response.status.state, TaskState.Failed)
        self.assertNotIn("secret-fake-key", json.dumps(response.model_dump(mode="json")))

    async def test_invalid_business_result_no_product_or_confirmation(self):
        async def invalid(task):
            result = await fixture_execute(task)
            result["subtaskId"] = "WRONG"
            return result
        app = create_local_business_app(execute=invalid, partner_aic=self.aic)
        runner = AipTaskRunner(sdk_sender(self.client(app)), "test-leader", self.aic, allow_mock_results=True)
        with self.assertRaisesRegex(AipExecutionError, "PARTNER_FAILED") as caught:
            await runner.run(search_task(), "bad-result")
        self.assertEqual([e["command"] for e in caught.exception.trace["events"]], ["start"])

    async def test_business_status_field_not_allowed(self):
        async def invalid(task):
            result = await fixture_execute(task)
            result["status"] = "custom-completed"
            return result
        app = create_local_business_app(execute=invalid, partner_aic=self.aic)
        response = await self.rpc(self.client(app), "start", search_task())
        self.assertEqual(response.status.state, TaskState.Failed)

    async def test_backend_timeout_does_not_complete(self):
        async def slow(task):
            await asyncio.sleep(1)
            return await fixture_execute(task)
        app = create_local_business_app(execute=slow, partner_aic=self.aic, backend_timeout_seconds=0.01)
        response = await self.rpc(self.client(app), "start", search_task())
        self.assertEqual(response.status.state, TaskState.Failed)
        self.assertIn("MAY_HAVE_OCCURRED", response.status.dataItems[0].text)

    async def test_cancel_during_execution_not_overwritten(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting(task):
            entered.set()
            await release.wait()
            return await fixture_execute(task)
        app = create_local_business_app(execute=waiting, partner_aic=self.aic)
        client, task_id = self.client(app), str(uuid4())
        start = asyncio.create_task(self.rpc(client, "start", search_task(), task_id=task_id))
        await asyncio.wait_for(entered.wait(), 1)
        response = await self.rpc(client, "cancel", task_id=task_id)
        self.assertEqual(response.status.state, TaskState.Canceled)
        release.set()
        response = await start
        self.assertEqual(response.status.state, TaskState.Canceled)
        self.assertFalse(response.products)

    async def test_failed_search_never_starts_reader(self):
        async def failure(task):
            self.calls.append(deepcopy(task))
            raise LocalBackendFailure("BACKEND_UNAVAILABLE")
        app = create_local_business_app(execute=failure, partner_aic=self.aic)
        with self.assertRaises(AipExecutionError):
            await run_fixture_chain("http://127.0.0.1/rpc", self.aic, transport=httpx.ASGITransport(app=app))
        self.assertEqual([t["taskType"] for t in self.calls], ["literature_search"])

    async def test_nonloopback_endpoint_rejected(self):
        for url in ("https://example.invalid/rpc", "http://name:secret@127.0.0.1/rpc",
                    "http://127.0.0.1/rpc?token=secret", "http://127.0.0.1/rpc#fragment"):
            with self.assertRaisesRegex(ValueError, "LOOPBACK"):
                await run_fixture_chain(url, self.aic)

    async def test_reader_wrong_paper_source_failed_before_confirmation(self):
        async def wrong_source(task):
            result = await fixture_execute(task)
            result["items"][0]["data"]["source"]["paperId"] = "wrong-paper"
            return result
        search = await fixture_execute(search_task())
        bound = bind_task_input(fixture_plan()["tasks"][1], {"S1": search})
        payload = {key: deepcopy(bound[key]) for key in BUSINESS_TASK_FIELDS}
        app = create_local_business_app(execute=wrong_source, partner_aic=self.aic)
        response = await self.rpc(self.client(app), "start", payload)
        self.assertEqual(response.status.state, TaskState.Failed)
        self.assertFalse(response.products)

    async def test_reader_empty_result_not_completed(self):
        async def empty(task):
            result = await fixture_execute(task)
            result["items"] = []
            return result
        search = await fixture_execute(search_task())
        bound = bind_task_input(fixture_plan()["tasks"][1], {"S1": search})
        payload = {key: deepcopy(bound[key]) for key in BUSINESS_TASK_FIELDS}
        app = create_local_business_app(execute=empty, partner_aic=self.aic)
        response = await self.rpc(self.client(app), "start", payload)
        self.assertEqual(response.status.state, TaskState.Failed)

    async def test_search_result_over_limit_failed(self):
        async def over_limit(task):
            result = await fixture_execute(task)
            result["items"] *= 2
            return result
        app = create_local_business_app(execute=over_limit, partner_aic=self.aic)
        response = await self.rpc(self.client(app), "start", search_task())
        self.assertEqual(response.status.state, TaskState.Failed)

    async def test_invalid_timeout_rejected(self):
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                create_local_business_app(execute=self.execute, partner_aic=self.aic,
                                          backend_timeout_seconds=value)

    async def test_mock_opt_in_must_be_explicit_boolean(self):
        with self.assertRaises(ValueError):
            AipTaskRunner(sdk_sender(self.client()), "test-leader", self.aic, allow_mock_results="true")

    async def test_wrong_partner_identity_rejected(self):
        runner = AipTaskRunner(sdk_sender(self.client()), "test-leader", "wrong-partner", allow_mock_results=True)
        with self.assertRaisesRegex(AipExecutionError, "CORRELATION"):
            await runner.run(search_task(), "wrong-partner")

    async def test_fixture_http_does_not_make_external_requests(self):
        from unittest.mock import patch
        # ASGI HTTP bypasses real sockets; any actual httpx network transport is a failure.
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("external network")):
            record = await run_fixture_chain("http://127.0.0.1/rpc", self.aic,
                                             transport=httpx.ASGITransport(app=self.app))
        self.assertTrue(record["acceptance"]["passed"])

    async def test_complete_is_idempotent(self):
        client, task_id = self.client(), str(uuid4())
        await self.rpc(client, "start", search_task(), task_id=task_id)
        for _ in range(2):
            result = await self.rpc(client, "complete", task_id=task_id)
            self.assertEqual(result.status.state, TaskState.Completed)
        self.assertEqual(len(self.calls), 1)
