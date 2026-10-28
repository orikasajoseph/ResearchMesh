"""Offline analysis Group tests: real SDK cleanup, fake workers and resources."""
import asyncio
import logging
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import json
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import AsyncMock, patch

from acps_sdk.aip import (GroupMgmtCommand, GroupMgmtCommandType, GroupPartnerMqClient,
                          TaskCommand, TaskCommandType, TaskResult, TaskState, StructuredDataItem)
from aio_pika.exceptions import AMQPConnectionError
from aip.audited_group_client import AuditedGroupPartnerMqClient
from aip.group_analysis_partner import serve_analysis
from aip.group_reader_handler import ReaderGroupHandler
from aip.group_repository_handler import RepositoryGroupHandler
from aip.group_task_store import DurableTaskStore
from aip.literature_audit import AuditJournal, read_events, task_scope
from aip.reader_unified_executor import ReaderExecutor
from aip.repository_unified_executor import RepositoryStaticExecutor
from common.models import BusinessTask
from tests.test_aip_reader import note_result
from verify_reader_aip import fixture_task
from test_group_repository_platform import task_payload, valid_result
from verify_literature_acceptance import verify

PARTNER, LEADER, GROUP, SESSION, TASK = "analysis-partner", "analysis-leader", "analysis-group", "analysis-session", "analysis-task"


def command(payload, action=TaskCommandType.Start, task_id=TASK, sender=LEADER):
    return TaskCommand(id="command-offline", sentAt="2026-10-23T00:00:00Z", senderRole="leader",
        senderId=sender, groupId=GROUP, sessionId=SESSION, taskId=task_id, command=action,
        mentions=[PARTNER], dataItems=[StructuredDataItem(data=payload)])


def fake_client():
    client = AuditedGroupPartnerMqClient(PARTNER)
    client._group_id = GROUP
    for name in ("accept_task", "start_working", "submit_for_completion", "complete_task",
                 "reject_task", "fail_task", "cancel_task", "send_task_result"):
        setattr(client, name, AsyncMock())
    return client


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from aip.audited_group_client import SDK_LOGGER
        # Local HTTP server tests may globally disable existing loggers.
        self.enterContext(patch.object(SDK_LOGGER, "disabled", False))
        self.enterContext(patch.object(SDK_LOGGER, "filters", []))
        self.enterContext(patch.object(SDK_LOGGER, "level", logging.WARNING))
        previous_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous_disable)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = DurableTaskStore(self.root / "tasks.sqlite3")
        self.audit = AuditJournal(self.root, mode="offline")
        self.client = fake_client()

    def handler(self, execute=None, cls=ReaderGroupHandler):
        return cls(self.client, leader_aic=LEADER, task_store=self.store,
                   executor=execute or AsyncMock(return_value=note_result()), audit=self.audit)

    def state(self, task=TASK):
        return self.store.lookup(agent_aic=PARTNER, session_id=SESSION, task_id=task)

    async def test_reader_start_complete_and_restart_duplicate(self):
        execute = AsyncMock(return_value=note_result())
        handler = self.handler(execute)
        await handler.on_command(command(fixture_task()), True)
        await handler.on_command(command(fixture_task(), TaskCommandType.Complete), True)
        await self.handler(execute).on_command(command(fixture_task()), True)
        execute.assert_awaited_once()
        self.assertEqual(self.state(), "completed")
        saved = [e for e in read_events(self.audit.path) if e["event"] == "RESULT_SAVED"]
        self.assertEqual(saved[0]["result"]["taskType"], "paper_read")
        self.assertEqual(saved[0]["result"]["items"][0]["kind"], "paper_note")

    async def test_reader_missing_pdf_and_batch_rejected_before_claim(self):
        for variant in ("missing_pdf", "batch", "leader_only_field", "wrong_type"):
            payload = fixture_task()
            if variant == "missing_pdf":
                payload["input"]["papers"][0]["pdfUrl"] = None
            elif variant == "batch":
                payload["input"]["papers"] *= 2
            elif variant == "leader_only_field":
                payload["requiredSkill"] = "paper.analyze"
            else:
                payload = task_payload()
            execute = AsyncMock()
            await self.handler(execute).on_command(command(payload), True)
            execute.assert_not_awaited()
            self.assertIsNone(self.state())

    async def test_reader_source_mismatch_and_abstract_are_uncertain(self):
        for task_id, change in (("bad-source", "source"), ("abstract", "level")):
            result = note_result()
            if change == "source":
                result["items"][0]["data"]["source"]["paperId"] = "different"
            else:
                result["items"][0]["data"]["analysisLevel"] = "abstract"
            await self.handler(AsyncMock(return_value=result)).on_command(command(fixture_task(), task_id=task_id), True)
            self.assertEqual(self.state(task_id), "uncertain")
        self.client.submit_for_completion.assert_not_awaited()

    async def test_reader_concurrent_duplicate_never_repeats_backend(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def backend(task):
            entered.set()
            await release.wait()
            return note_result()
        execute = AsyncMock(side_effect=backend)
        handler = self.handler(execute)
        first = asyncio.create_task(handler.on_command(command(fixture_task()), True))
        await entered.wait()
        await handler.on_command(command(fixture_task()), True)
        release.set()
        await first
        execute.assert_awaited_once()

    async def test_transport_cancel_detaches_delivery_not_business_lifecycle(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def backend(task):
            entered.set()
            await release.wait()
            return note_result()
        execute = AsyncMock(side_effect=backend)
        client = AuditedGroupPartnerMqClient(PARTNER)
        client._group_id = GROUP
        client.audit = self.audit
        for name in ("accept_task", "start_working", "submit_for_completion", "fail_task"):
            setattr(client, name, AsyncMock())
        handler = ReaderGroupHandler(client, leader_aic=LEADER, task_store=self.store,
                                     executor=execute, audit=self.audit)
        client.set_command_handler(handler.on_command)
        body = command(fixture_task()).model_dump(mode="json")

        delivery = asyncio.create_task(client._handle_incoming_message(body))
        await entered.wait()
        delivery.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await delivery
        self.assertEqual(self.state(), "working")

        # Broker redelivery is acknowledged by the durable duplicate claim and
        # cannot start a second backend while the detached lifecycle continues.
        await client._handle_incoming_message(body)
        execute.assert_awaited_once()
        release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(client._command_lifecycles)), 3)
        execute.assert_awaited_once()
        client.submit_for_completion.assert_awaited_once()
        self.assertEqual(self.state(), "awaiting_completion")
        self.assertTrue(any(e["event"] == "COMMAND_DELIVERY_DETACHED"
                            for e in read_events(self.audit.path)))

    async def test_result_delivery_retries_same_message_after_transport_recovers(self):
        client = AuditedGroupPartnerMqClient(PARTNER)
        client._group_id = GROUP
        client.audit = self.audit
        sent = []
        async def publish(_client, message):
            sent.append(message.id)
            if len(sent) == 1:
                raise AMQPConnectionError("offline test")
        async def elapse(awaitable, *, timeout):
            awaitable.close()
            raise TimeoutError
        result = TaskResult(id="stable-result-id", sentAt="2026-10-23T00:00:00Z",
            senderRole="partner", senderId=PARTNER, groupId=GROUP, sessionId=SESSION,
            taskId=TASK, status={"state": TaskState.AwaitingCompletion,
                                "stateChangedAt": "2026-10-23T00:00:00Z"})
        with patch.object(GroupPartnerMqClient, "_publish_message", new=publish), \
             patch("aip.audited_group_client.asyncio.wait_for", new=elapse):
            await client._publish_message(result)
        self.assertEqual(sent, ["stable-result-id", "stable-result-id"])
        deferred = [e for e in read_events(self.audit.path)
                    if e["event"] == "RESULT_DELIVERY_DEFERRED"]
        self.assertEqual(len(deferred), 1)

    async def test_detached_business_exception_is_reaped_and_remains_uncertain(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def backend(task):
            entered.set()
            await release.wait()
            raise RuntimeError("SECRET")
        client = AuditedGroupPartnerMqClient(PARTNER)
        client._group_id = GROUP
        client.audit = self.audit
        for name in ("accept_task", "start_working", "submit_for_completion", "fail_task"):
            setattr(client, name, AsyncMock())
        handler = ReaderGroupHandler(client, leader_aic=LEADER, task_store=self.store,
                                     executor=backend, audit=self.audit)
        client.set_command_handler(handler.on_command)
        delivery = asyncio.create_task(client._handle_incoming_message(
            command(fixture_task()).model_dump(mode="json")))
        await entered.wait()
        delivery.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await delivery
        release.set()
        active = tuple(client._command_lifecycles)
        await asyncio.wait_for(asyncio.gather(*active), 3)
        self.assertFalse(client._command_lifecycles)
        self.assertEqual(self.state(), "uncertain")
        client.fail_task.assert_awaited_once()
        client.submit_for_completion.assert_not_awaited()
        self.assertNotIn("SECRET", self.audit.path.read_text(encoding="utf-8"))

    async def test_cleanup_cancels_and_reaps_detached_business_lifecycle(self):
        entered = asyncio.Event()
        async def backend(task):
            entered.set()
            await asyncio.Event().wait()
        client = AuditedGroupPartnerMqClient(PARTNER)
        client._group_id = GROUP
        client.audit = self.audit
        for name in ("accept_task", "start_working", "submit_for_completion", "fail_task"):
            setattr(client, name, AsyncMock())
        handler = ReaderGroupHandler(client, leader_aic=LEADER, task_store=self.store,
                                     executor=backend, audit=self.audit)
        client.set_command_handler(handler.on_command)
        delivery = asyncio.create_task(client._handle_incoming_message(
            command(fixture_task()).model_dump(mode="json")))
        await entered.wait()
        delivery.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await delivery
        with patch.object(GroupPartnerMqClient, "_cleanup_group_resources", new=AsyncMock()):
            await client._cleanup_group_resources()
        self.assertFalse(client._command_lifecycles)
        self.assertEqual(self.state(), "uncertain")

    async def test_cancel_blocks_product_after_backend_returns(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def backend(task):
            entered.set()
            await release.wait()
            return note_result()
        handler = self.handler(backend)
        first = asyncio.create_task(handler.on_command(command(fixture_task()), True))
        await entered.wait()
        await handler.on_command(command(fixture_task(), TaskCommandType.Cancel), True)
        release.set()
        await first
        self.assertEqual(self.state(), "canceled")
        self.client.submit_for_completion.assert_not_awaited()

    async def test_untrusted_leader_never_claims_or_executes(self):
        execute = AsyncMock()
        await self.handler(execute).on_command(command(fixture_task(), sender="wrong-leader"), True)
        execute.assert_not_awaited()
        self.assertIsNone(self.state())

    async def test_backend_exception_is_uncertain_and_not_retried(self):
        execute = AsyncMock(side_effect=RuntimeError("SECRET"))
        handler = self.handler(execute)
        await handler.on_command(command(fixture_task()), True)
        await handler.on_command(command(fixture_task()), True)
        execute.assert_awaited_once()
        self.assertEqual(self.state(), "uncertain")
        self.assertNotIn("SECRET", self.audit.path.read_text())

    async def test_complete_send_failure_never_records_completed(self):
        handler = self.handler()
        await handler.on_command(command(fixture_task()), True)
        self.client.complete_task.side_effect = RuntimeError("SECRET")
        with self.assertRaises(RuntimeError):
            await handler.on_command(command(fixture_task(), TaskCommandType.Complete), True)
        self.assertEqual(self.state(), "uncertain")
        self.assertFalse(any(e["event"] == "SQLITE_COMPLETED" for e in read_events(self.audit.path)))

    async def test_reader_executor_uses_fresh_worker_context_per_task(self):
        python = self.root / "python.exe"; python.touch()
        wrapper = self.root / "reader" / "researchmesh_paper_reader"; wrapper.mkdir(parents=True)
        (wrapper / "__init__.py").touch()
        calls = []
        def factory(**kwargs):
            self.assertTrue(kwargs["dynamic"])
            self.assertIsNone(kwargs["resume_journal"])
            calls.append(kwargs["record_dir"])
            async def backend(task):
                result = note_result(task); result["subtaskId"] = task["subtaskId"]
                return result
            return backend
        with patch("aip.reader_unified_executor.ROOT", self.root), patch("aip_reader_partner.ROOT", self.root), \
             patch("aip.reader_unified_executor.create_reader_business_executor", side_effect=factory):
            executor = ReaderExecutor(reader_python=python, reader_root=wrapper.parent)
            await executor(BusinessTask.model_validate(fixture_task()))
            task = fixture_task(); task["subtaskId"] = "S2-new"
            await executor(BusinessTask.model_validate(task))
        self.assertEqual(len(set(calls)), 2)

    async def test_reader_executor_passes_protocol_identity_and_worker_run_id(self):
        python = self.root / "python.exe"; python.touch()
        wrapper = self.root / "reader" / "researchmesh_paper_reader"; wrapper.mkdir(parents=True)
        (wrapper / "__init__.py").touch()
        captured = {}
        def factory(**kwargs):
            captured.update(kwargs)
            async def backend(task):
                result = note_result(task); result["subtaskId"] = task["subtaskId"]
                return result
            return backend
        identity = {"groupId": GROUP, "sessionId": SESSION, "taskId": TASK,
                    "agentAic": PARTNER}
        with patch("aip.reader_unified_executor.ROOT", self.root), \
             patch("aip_reader_partner.ROOT", self.root), \
             patch("aip.reader_unified_executor.create_reader_business_executor", side_effect=factory):
            executor = ReaderExecutor(reader_python=python, reader_root=wrapper.parent)
            with task_scope(self.audit, identity):
                await executor(BusinessTask.model_validate(fixture_task()))
        self.assertEqual(captured["audit_identity"], identity)
        self.assertRegex(captured["worker_run_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(captured["record_dir"].name,
                         "reader-group-" + captured["worker_run_id"])

    async def test_real_sdk_simulated_lifecycle_for_both_agents(self):
        for agent_type in ("paper_read", "repository_analyze"):
            with self.subTest(agent_type=agent_type), TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = DurableTaskStore(root / "claims.sqlite3")
                audit = AuditJournal(root, mode="offline")
                client = fake_client()
                client._group_id = None
                ready, restarted = asyncio.Event(), asyncio.Event()
                client.connect = AsyncMock()
                client.close = AsyncMock()
                client.send_status_result = AsyncMock()
                async def inbox(callback):
                    async def join(invitation):
                        client._group_id = GROUP
                        return True
                    client.join_group_from_invitation = join
                    await callback(object())
                    ready.set()
                client.start_inbox_consuming = inbox
                async def close_channel():
                    client._channel.is_closed = True
                async def close_connection():
                    client._connection.is_closed = True
                client._channel = SimpleNamespace(is_closed=False, close=AsyncMock(side_effect=close_channel))
                client._connection = SimpleNamespace(is_closed=False, close=AsyncMock(side_effect=close_connection))
                client._queue = SimpleNamespace(name="offline-queue", cancel=AsyncMock(), delete=AsyncMock())
                client._consumer_tag = "offline-consumer"
                second = fake_client()
                second._group_id = None
                second.connect = AsyncMock()
                second.close = AsyncMock()
                async def second_inbox(callback):
                    restarted.set()
                second.start_inbox_consuming = second_inbox
                python = root / "python.exe"; python.touch()
                name = "researchmesh_paper_reader" if agent_type == "paper_read" else "researchmesh_repository"
                wrapper = root / "wrapper" / name; wrapper.mkdir(parents=True)
                (wrapper / "__init__.py").touch()
                if agent_type == "paper_read":
                    handler_cls = ReaderGroupHandler
                    payload = fixture_task()
                    executor = ReaderExecutor(reader_python=python, reader_root=wrapper.parent)
                    target = "aip.reader_unified_executor.create_reader_business_executor"
                    backend = AsyncMock(return_value=note_result())
                    root_target = "aip.reader_unified_executor.ROOT"
                else:
                    handler_cls = RepositoryGroupHandler
                    payload = task_payload()
                    executor = RepositoryStaticExecutor(repository_python=python, wrapper_root=wrapper.parent)
                    target = "aip.repository_unified_executor.create_repository_business_executor"
                    backend = AsyncMock(return_value=valid_result(BusinessTask.model_validate(payload)))
                    root_target = "aip.repository_unified_executor.ROOT"
                with patch("aip.group_analysis_partner.AuditedGroupPartnerMqClient", side_effect=[client, second]), \
                     patch("aip.group_analysis_partner.tls_context", return_value=object()), \
                     patch(root_target, root), patch(target, return_value=backend):
                    server = asyncio.create_task(serve_analysis(partner_aic=PARTNER, leader_aic=LEADER,
                        credential_dir=root, task_store=store, executor=executor, handler_type=handler_cls, audit=audit))
                    try:
                        await asyncio.wait_for(ready.wait(), 3)
                        await client._command_handler(command(payload), True)
                        await client._command_handler(command(payload), True)
                        await client._command_handler(command(payload, TaskCommandType.Complete), True)
                        disband = GroupMgmtCommand(id="disband", sentAt="2026-10-23T00:00:00Z", senderRole="leader",
                            senderId=LEADER, groupId=GROUP, sessionId=SESSION, mentions=[PARTNER], command=GroupMgmtCommandType.DISBAND)
                        await client._handle_incoming_message(disband.model_dump(mode="json"))
                        await client._handle_leave_request()
                        await asyncio.wait_for(restarted.wait(), 3)
                        self.assertFalse(server.done())
                        client.close.assert_awaited_once()
                        self.assertTrue(any(e["event"] == "INBOX_RESTART_SUCCEEDED"
                                            for e in read_events(audit.path)))
                        backend.assert_awaited_once()
                        report = verify(audit.path, store.path, group=GROUP, session=SESSION, task=TASK,
                            agent=PARTNER, require_formal=False, require_http=False, expected_task_type=agent_type)
                        self.assertTrue(report["accepted"], report)
                        self.assertNotIn("exactly_one_actual_http", report["checks"])
                        # Provider/model count remains unmeasured; offline evidence never formal proof.
                        formal = verify(audit.path, store.path, group=GROUP, session=SESSION, task=TASK,
                            agent=PARTNER, require_http=False, expected_task_type=agent_type)
                        self.assertFalse(formal["accepted"])
                        audit.emit("BACKEND_STARTED", groupId=GROUP, sessionId=SESSION, taskId=TASK, agentAic=PARTNER)
                        self.assertFalse(verify(audit.path, store.path, group=GROUP, session=SESSION, task=TASK,
                            agent=PARTNER, require_formal=False, require_http=False, expected_task_type=agent_type)["accepted"])
                    finally:
                        server.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await server

    def test_reader_cli_offline_never_probes_runtime_or_starts_partner(self):
        import run_group_reader as cli
        env = dict(RM_READER_AIC=PARTNER, RM_ALLOWED_LEADER_AIC=LEADER, RM_READER_PYTHON="test-python")
        with patch("sys.argv", ["run_group_reader.py"]), patch.dict("os.environ", env), \
             patch.object(cli, "load_dotenv"), patch.object(cli, "tls_context"), \
             patch.object(cli.ReaderExecutor, "validate_runtime_paths"), \
             patch.object(cli, "run_live") as run, \
             patch("scripts.check_analysis_group_readiness.runtime_probe") as probe, redirect_stdout(StringIO()):
            self.assertEqual(cli.main(), 0)
            run.assert_not_called(); probe.assert_not_called()

    def test_reader_cli_failed_runtime_blocks_mq_before_serve(self):
        import run_group_reader as cli
        env = dict(RM_READER_AIC=PARTNER, RM_ALLOWED_LEADER_AIC=LEADER, RM_READER_PYTHON="test-python")
        with patch("sys.argv", ["run_group_reader.py", "--serve"]), patch.dict("os.environ", env), \
             patch.object(cli, "load_dotenv"), patch.object(cli, "tls_context"), \
             patch.object(cli.ReaderExecutor, "validate_runtime_paths"), \
             patch.object(cli, "run_live") as run, \
             patch("scripts.check_analysis_group_readiness.runtime_probe", return_value={"passed":False,"code":"TEST"}), redirect_stdout(StringIO()):
            self.assertEqual(cli.main(), 2)
            run.assert_not_called()

    def test_repository_cli_failed_runtime_blocks_mq_before_serve(self):
        import run_group_repository as cli
        env = dict(RM_REPOSITORY_AIC=PARTNER, RM_ALLOWED_LEADER_AIC=LEADER, RM_REPOSITORY_PYTHON="test-python")
        with patch("sys.argv", ["run_group_repository.py", "--serve"]), patch.dict("os.environ", env), \
             patch.object(cli, "load_dotenv"), patch.object(cli, "tls_context"), \
             patch.object(cli.RepositoryStaticExecutor, "validate_runtime_paths"), \
             patch.object(cli, "run_live") as run, \
             patch("scripts.check_analysis_group_readiness.runtime_probe", return_value={"passed":False,"code":"TEST"}), redirect_stdout(StringIO()):
            self.assertEqual(cli.main(), 2)
            run.assert_not_called()

    def test_analysis_report_does_not_claim_provider_http_count(self):
        from verify_analysis_acceptance import main
        output = self.root / "analysis-report.json"
        args = ["verify", "--journal", str(self.root / "missing"), "--database", str(self.root / "missing.db"),
                "--group", GROUP, "--session", SESSION, "--task", TASK, "--agent", PARTNER,
                "--task-type", "paper_read", "--report", str(output)]
        with patch("sys.argv", args), redirect_stdout(StringIO()):
            self.assertEqual(main(), 1)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["accepted"])
            self.assertFalse(report["providerHttpCountVerified"])
            with self.assertRaises(FileExistsError):
                main()

    def test_runtime_probe_failure_never_persists_exception_text(self):
        from scripts.check_analysis_group_readiness import runtime_probe
        with patch("scripts.check_analysis_group_readiness.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stdout="SECRET")):
            report = runtime_probe("python", "reader", "env", "cache")
        self.assertFalse(report["passed"])
        self.assertNotIn("SECRET", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
