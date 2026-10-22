"""Actual SDK lifecycle + HTTP MockTransport; all MQ operations are fakes."""
import asyncio
from copy import deepcopy
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from acps_sdk.aip import GroupMgmtCommand, GroupMgmtCommandType, TaskCommandType

from aip.audited_group_client import AuditedGroupPartnerMqClient
from aip.group_literature_partner import serve
from aip.group_literature_handler import LiteratureGroupHandler
from aip.group_task_store import DurableTaskStore
from aip.literature_audit import AuditJournal, read_events, task_scope
from semantic_http import ObservedRequester, ProcessRateGate
from test_group_unified_wiring import business_task, business_result, command, GROUP, SESSION, TASK, PARTNER, LEADER
from verify_literature_acceptance import verify
from verify_literature_acceptance import main as verify_main
from business_execution import execute_business_task, BusinessExecutionError


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audit = AuditJournal(self.root, mode="offline")
        self.identity = dict(groupId=GROUP, sessionId=SESSION, taskId=TASK, agentAic=PARTNER)

    def events(self, name):
        return [e for e in read_events(self.audit.path) if e["event"] == name]

    def test_append_preserves_previous_journals_and_readback_redacts(self):
        before = self.audit.path.read_bytes()
        other = AuditJournal(self.root, mode="offline")
        self.assertNotEqual(other.path, self.audit.path)
        self.assertEqual(before, self.audit.path.read_bytes())
        result = business_result()
        result["summary"] = "SECRET"
        result["warnings"] = ["SECRET"]
        result["items"][0]["data"]["url"] = "https://example.com/?token=SECRET"
        self.audit.save_result(self.identity, business_task(), result)
        self.assertNotIn("SECRET", self.audit.path.read_text())
        self.assertEqual(len(self.events("RESULT_READBACK_VALID")), 1)
        self.assertEqual(len(self.events("RESULT_SAVED")[0]["result"]), 6)

    def test_invalid_result_does_not_save(self):
        result = business_result()
        result["extra"] = True
        with self.assertRaises(ValueError):
            self.audit.save_result(self.identity, business_task(), result)
        self.assertFalse(self.events("RESULT_SAVED"))

    def test_fsync_failure_is_not_silently_accepted(self):
        with patch("aip.literature_audit.os.fsync", side_effect=OSError("test")):
            with self.assertRaises(OSError):
                self.audit.emit("TEST")

    def test_cli_invalid_evidence_saves_failure_report_without_overwrite(self):
        path = self.root / "report.json"
        arguments = ["verify", "--journal", str(self.root / "missing"), "--database", str(self.root / "missing.db"),
                     "--group", GROUP, "--session", SESSION, "--task", TASK, "--agent", PARTNER,
                     "--report", str(path)]
        with patch("sys.argv", arguments), redirect_stdout(StringIO()):
            self.assertEqual(verify_main(), 1)
            saved = path.read_bytes()
            self.assertFalse(json.loads(saved)["accepted"])
            with self.assertRaises(FileExistsError):
                verify_main()
            self.assertEqual(path.read_bytes(), saved)

    async def test_actual_http_errors_count_and_never_store_secrets(self):
        for status in (200, 429, 500):
            requester = ObservedRequester(1, "SECRET", gate=ProcessRateGate(0),
                transport=httpx.MockTransport(lambda r: httpx.Response(status,
                    headers={"Authorization": "SECRET"}, json={"message": "SECRET"})))
            with task_scope(self.audit, self.identity):
                try:
                    await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search",
                                                   "query=SECRET", {"x-api-key": "SECRET"})
                except Exception:
                    if status == 200:
                        raise
        self.assertEqual([e["requestSequence"] for e in self.events("HTTP_STARTED")], [1, 2, 3])
        self.assertEqual([e["httpStatus"] for e in self.events("HTTP_FINISHED")], [200, 429, 500])
        self.assertNotIn("SECRET", self.audit.path.read_text())

    async def test_transport_failure_and_cancellation_persist(self):
        async def fail(request):
            raise httpx.ConnectError("SECRET")
        async def cancel(request):
            raise asyncio.CancelledError()
        for transport in (fail, cancel):
            requester = ObservedRequester(1, None, gate=ProcessRateGate(0), transport=httpx.MockTransport(transport))
            with task_scope(self.audit, self.identity):
                with self.assertRaises((RuntimeError, asyncio.CancelledError)):
                    await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})
        self.assertEqual([e["exceptionCategory"] for e in self.events("HTTP_FINISHED")], ["CONNECT_ERROR", "CANCELLED"])
        self.assertNotIn("SECRET", self.audit.path.read_text())

    async def test_concurrent_tasks_keep_independent_http_identity_and_sequence(self):
        async def invoke(task):
            identity = dict(self.identity, taskId=task)
            requester = ObservedRequester(1, None, gate=ProcessRateGate(0),
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
            with task_scope(self.audit, identity):
                await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})
        await asyncio.gather(invoke("task-a"), invoke("task-b"))
        starts = self.events("HTTP_STARTED")
        self.assertEqual({e["taskId"] for e in starts}, {"task-a", "task-b"})
        self.assertEqual([e["requestSequence"] for e in starts], [1, 1])

    async def test_backend_timeout_is_observed_without_retry(self):
        backend = AsyncMock(side_effect=lambda payload: None)
        async def wait(payload):
            await asyncio.Event().wait()
        backend.side_effect = wait
        with task_scope(self.audit, self.identity):
            with self.assertRaises(BusinessExecutionError):
                await execute_business_task(business_task(), skill="literature.search", backend=backend,
                                            allow_execution=True, timeout_seconds=.01)
        backend.assert_awaited_once()
        self.assertEqual(len(self.events("BACKEND_STARTED")), 1)
        self.assertEqual(self.events("BACKEND_FINISHED")[0]["outcome"], "cancelled")

    async def test_audit_failure_blocks_actual_http_send(self):
        sent = []
        requester = ObservedRequester(1, None, gate=ProcessRateGate(0),
            transport=httpx.MockTransport(lambda r: sent.append(r)))
        with (task_scope(self.audit, self.identity),
              patch.object(self.audit, "request_started", side_effect=OSError("test"))):
            with self.assertRaises(OSError):
                await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "", {})
        self.assertEqual(sent, [])

    async def setup_partner(self):
        store = DurableTaskStore(self.root / "tasks.sqlite3")
        client = AuditedGroupPartnerMqClient(PARTNER)
        ready = asyncio.Event()
        for method in ("connect", "accept_task", "start_working", "submit_for_completion",
                       "complete_task", "fail_task", "reject_task", "send_status_result", "close"):
            setattr(client, method, AsyncMock())
        async def inbox(callback):
            self.invitation_callback = callback
            ready.set()
        client.start_inbox_consuming = inbox
        async def join(invitation):
            client._group_id = GROUP
            return True
        client.join_group_from_invitation = join
        server = asyncio.create_task(serve(partner_aic=PARTNER, leader_aic=LEADER,
            credential_dir=self.root, task_store=store, audit=self.audit))
        await asyncio.wait_for(ready.wait(), 3)
        await self.invitation_callback(object())
        client._consumer_tag = "test-consumer"
        client._queue = SimpleNamespace(name="test-queue", cancel=AsyncMock(), delete=AsyncMock())
        client._channel = SimpleNamespace(is_closed=False)
        client._connection = SimpleNamespace(is_closed=False)
        async def close_channel():
            client._channel.is_closed = True
        async def close_connection():
            client._connection.is_closed = True
        client._channel.close = AsyncMock(side_effect=close_channel)
        client._connection.close = AsyncMock(side_effect=close_connection)
        return client, store, server

    async def lifecycle(self, client):
        disband = GroupMgmtCommand(id="disband-offline", sentAt="2026-10-22T00:00:00Z",
            senderRole="leader", senderId=LEADER, groupId=GROUP, sessionId=SESSION,
            mentions=[PARTNER], command=GroupMgmtCommandType.DISBAND)
        await client._handle_incoming_message(disband.model_dump(mode="json"))
        self.assertTrue(client._pending_leave)
        # SDK's message consumer defers this until its message ACK has completed.
        await client._handle_leave_request()

    async def stop(self, server):
        server.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await server

    async def test_full_group_lifecycle_and_acceptance_rejects_missing_or_duplicate_evidence(self):
        sent = []
        def http(request):
            sent.append(request)
            return httpx.Response(200, json={})
        requester = ObservedRequester(1, None, gate=ProcessRateGate(0), transport=httpx.MockTransport(http))
        async def pipeline(*args, **kwargs):
            await requester.get_data_async("https://api.semanticscholar.org/graph/v1/paper/search", "query=offline", {})
            return {"error": None, "result": business_result()}
        client = AuditedGroupPartnerMqClient(PARTNER)
        with (patch("aip.group_literature_partner.GroupPartnerMqClient", return_value=client),
              patch("aip.group_literature_partner.tls_context", return_value=object()),
              patch("aip.literature_unified_executor.execute_live_task", side_effect=pipeline)):
            # setup_partner constructs its own client; supply the identical SDK instance.
            with patch(__name__ + ".AuditedGroupPartnerMqClient", return_value=client):
                client, store, server = await self.setup_partner()
            try:
                await client._command_handler(command(), True)
                await client._command_handler(command(), True)
                await client._command_handler(command(TaskCommandType.Complete), True)
                # Restarted handler also cannot repeat the backend.
                restarted = LiteratureGroupHandler(client, leader_aic=LEADER, task_store=store,
                    executor=AsyncMock(side_effect=AssertionError("duplicate backend")), audit=self.audit)
                await restarted.on_command(command(), True)
                await self.lifecycle(client)
                self.assertFalse(server.done())  # Group leave must not terminate the AMQPS app.
                client.close.assert_not_awaited()
                self.assertEqual(len(sent), 1)
                check = lambda path=self.audit.path: verify(path, store.path, group=GROUP, session=SESSION,
                    task=TASK, agent=PARTNER, require_formal=False)
                report = check()
                self.assertTrue(report["accepted"], report)
                self.assertFalse(verify(self.audit.path, store.path, group=GROUP, session=SESSION,
                    task=TASK, agent=PARTNER)["accepted"])  # offline evidence never formal proof
                original = read_events(self.audit.path)
                for missing in ("HTTP_FINISHED", "RESULT_SAVED", "GROUP_LEFT", "CLEANUP_SUCCEEDED", "BACKEND_FINISHED"):
                    changed = [e for e in original if e["event"] != missing]
                    for number, event in enumerate(changed, 1):
                        event = dict(event, sequence=number)
                        changed[number - 1] = event
                    path = self.root / (missing + ".jsonl")
                    path.write_text("".join(json.dumps(e) + "\n" for e in changed))
                    self.assertFalse(check(path)["accepted"], missing)
                self.audit.emit("BACKEND_STARTED", **self.identity)
                self.assertFalse(check()["accepted"])
            finally:
                await self.stop(server)

    async def test_sdk_swallowed_delete_failure_fails_cleanup_evidence(self):
        client = AuditedGroupPartnerMqClient(PARTNER)
        with (patch("aip.group_literature_partner.GroupPartnerMqClient", return_value=client),
              patch("aip.group_literature_partner.tls_context", return_value=object()),
              patch(__name__ + ".AuditedGroupPartnerMqClient", return_value=client)):
            client, _, server = await self.setup_partner()
            try:
                client._queue.delete.side_effect = RuntimeError("SECRET")
                await self.lifecycle(client)
                failures = self.events("CLEANUP_FAILED")
                self.assertEqual(failures[0]["failures"], ["queue_delete_failed"])
                self.assertFalse(self.events("CLEANUP_SUCCEEDED"))
                self.assertNotIn("SECRET", self.audit.path.read_text())
            finally:
                await self.stop(server)

    async def test_raised_cleanup_failure_and_residual_resources(self):
        client = AuditedGroupPartnerMqClient(PARTNER)
        client.audit = self.audit
        client._group_id = GROUP
        client._channel = SimpleNamespace(is_closed=False, close=AsyncMock(side_effect=RuntimeError("SECRET")))
        with self.assertRaises(RuntimeError):
            await client._cleanup_group_resources()
        self.assertIn("_channel", self.events("CLEANUP_FAILED")[0]["residual"])
        self.assertNotIn("SECRET", self.audit.path.read_text())

    async def test_suppressed_sdk_warning_logs_cannot_claim_cleanup_success(self):
        from aip.audited_group_client import SDK_LOGGER
        client = AuditedGroupPartnerMqClient(PARTNER)
        client.audit = self.audit
        client._group_id = GROUP
        with patch.object(SDK_LOGGER, "disabled", True):
            await client._cleanup_group_resources()
        self.assertEqual(self.events("CLEANUP_FAILED")[0]["failures"], ["sdk_warning_observation_unavailable"])
        self.assertFalse(self.events("CLEANUP_SUCCEEDED"))

    async def test_unclosed_channel_is_not_success_after_sdk_reset(self):
        client = AuditedGroupPartnerMqClient(PARTNER)
        client.audit = self.audit
        client._group_id = GROUP
        client._channel = SimpleNamespace(is_closed=False, close=AsyncMock())
        await client._cleanup_group_resources()
        self.assertIn("channel_not_closed", self.events("CLEANUP_FAILED")[0]["residual"])


if __name__ == "__main__":
    unittest.main()
