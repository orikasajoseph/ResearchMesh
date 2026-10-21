"""Offline Group lifecycle and official SDK messages; no live platform/API calls."""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from acps_sdk.aip import GroupMgmtCommandType, Product, StructuredDataItem, TaskCommandType, TaskResult, TaskState
from acps_sdk.aip.aip_group_auth import GroupAclError
from acps_sdk.aip.aip_group_identity import assert_incoming_group_message_identity
from acps_sdk.aip.aip_identity import SenderIdentityMismatchError

import group_literature_leader as leader
import run_platform_literature as cli
from tests.test_group_invitation import LEADER, PARTNER, config, joined


TASK = {
    "schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
    "goal": "Find one LoRA paper", "input": {"query": "LoRA"},
    "constraints": {"maxResults": 1}, "outputSchema": "paper_records.v1",
}


def result():
    return {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
            "summary": "Offline fixture, no Provider called", "warnings": [],
            "items": [{"kind": "paper", "data": {"paperId": "arxiv:2106.09685", "title": "LoRA",
                       "doi": None, "url": "https://arxiv.org/abs/2106.09685", "pdfUrl": None,
                       "repositoryUrls": []}}]}


def message(group_id, task_id, session_id, state, data=None, *, message_id=None):
    return TaskResult(id=message_id or "result-" + state.value,
        sentAt=datetime.now(timezone.utc).isoformat(), senderRole="partner", senderId=PARTNER,
        groupId=group_id, taskId=task_id, sessionId=session_id,
        status={"state": state, "stateChangedAt": datetime.now(timezone.utc).isoformat()},
        products=[Product(id="papers", dataItems=[StructuredDataItem(data=data)])] if data is not None else None)


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, *, data=None, final=None, wrong=False, early=False,
                       terminal=None, duplicate=False, silent=False, publish_error=False):
        convo = leader.LiteratureConversation(config(), "g", "t", "s")
        rec = {"startAttempted": False, "taskCommandsSent": 0, "startPublished": False,
               "completeAttempted": False, "completePublished": False,
               "taskStates": [], "result": None, "resultValidated": False,
               "protocolCompleted": False, "businessReady": False}
        data = result() if data is None else data
        commands = []

        async def send(command, task_id, session_id, **kwargs):
            commands.append((command, kwargs))
            if publish_error:
                raise RuntimeError("secret-publish-error")
            if silent:
                return
            if command == TaskCommandType.Start:
                if wrong:
                    m = message("wrong-group", "t", "s", TaskState.AwaitingCompletion, data)
                    await convo.on_message(m)
                if early:
                    await convo.on_message(message("g", "t", "s", TaskState.Completed, data))
                    return
                if terminal:
                    await convo.on_message(message("g", "t", "s", terminal))
                    return
                await convo.on_message(message("g", "t", "s", TaskState.Accepted))
                await convo.on_message(message("g", "t", "s", TaskState.Working))
                await convo.on_message(message("g", "t", "s", TaskState.AwaitingCompletion, data))
                if duplicate:
                    await convo.on_message(message("g", "t", "s", TaskState.AwaitingCompletion, data))
                    await convo.on_message(message("g", "t", "s", TaskState.AwaitingCompletion, data, message_id="duplicate-state"))
            else:
                await convo.on_message(message("g", "t", "s", TaskState.Completed, final))

        client = MagicMock()
        client.send_task_command = AsyncMock(side_effect=send)
        events = []
        try:
            await convo.run(client, TASK, rec, events.append, .03)
            error = None
        except Exception as exc:
            error = exc
        return rec, commands, error, convo

    async def test_success_one_start_one_complete_with_seven_fields(self):
        rec, commands, error, _ = await self.scenario()
        self.assertIsNone(error)
        self.assertTrue(rec["protocolCompleted"])
        self.assertTrue(rec["businessReady"])
        self.assertEqual([c[0] for c in commands], [TaskCommandType.Start, TaskCommandType.Complete])
        self.assertEqual(commands[0][1]["mentions"], [PARTNER])
        self.assertEqual(commands[0][1]["data_items"][0].data, TASK)
        self.assertEqual(len(commands[0][1]["data_items"][0].data), 7)
        self.assertEqual(rec["result"], result())

    async def test_duplicates_do_not_resend_complete(self):
        rec, commands, error, _ = await self.scenario(duplicate=True)
        self.assertIsNone(error)
        self.assertEqual(len(commands), 2)

    async def test_wrong_identifiers_ignored(self):
        rec, _, error, convo = await self.scenario(wrong=True)
        self.assertIsNone(error)
        self.assertTrue(rec["protocolCompleted"])
        self.assertEqual(convo.ignored, 1)
        for field in ("senderId", "taskId", "sessionId", "senderRole"):
            m = message("g", "t", "s", TaskState.Working).model_copy(update={field: "leader" if field == "senderRole" else "other"})
            await convo.on_message(m)
        self.assertEqual(convo.ignored, 5)

    async def test_completed_without_handshake_is_not_success(self):
        rec, commands, error, _ = await self.scenario(early=True)
        self.assertEqual(str(error), "COMPLETION_HANDSHAKE_MISSING")
        self.assertFalse(rec["protocolCompleted"])
        self.assertEqual(len(commands), 1)

    async def test_invalid_or_mock_product_never_completed(self):
        changes = [dict(subtaskId="wrong"), dict(status="completed"), dict(schemaVersion="0.1"),
                   dict(warnings=["TRANSPORT_TEST_ONLY fixture"]),
                   dict(warnings=["SIMULATED_RESEARCH fixture"])]
        simulated = result()
        simulated["items"][0]["data"]["isMock"] = True
        for data in [dict(result(), **change) for change in changes] + [simulated]:
            with self.subTest(data=data):
                rec, commands, error, _ = await self.scenario(data=data)
                self.assertIsNotNone(error)
                self.assertFalse(rec["protocolCompleted"])
                self.assertEqual(len(commands), 1)

    async def test_changed_final_product_is_rejected(self):
        final = result()
        final["summary"] = "Changed after Complete"
        rec, _, error, _ = await self.scenario(final=final)
        self.assertEqual(str(error), "PRODUCT_CHANGED_AFTER_CONFIRMATION")
        self.assertFalse(rec["protocolCompleted"])

    async def test_empty_search_is_completed_but_not_business_ready(self):
        data = dict(result(), items=[], warnings=["NO_RESULTS"])
        rec, _, error, _ = await self.scenario(data=data)
        self.assertIsNone(error)
        self.assertTrue(rec["protocolCompleted"])
        self.assertFalse(rec["businessReady"])

    async def test_partner_failures_stop_without_complete(self):
        for state in (TaskState.Failed, TaskState.Rejected, TaskState.Canceled, TaskState.AwaitingInput):
            rec, commands, error, _ = await self.scenario(terminal=state)
            self.assertIsNotNone(error)
            self.assertEqual(len(commands), 1)
            self.assertFalse(rec["protocolCompleted"])

    async def test_timeout_and_publish_failure_never_retry_start(self):
        for option in ({"silent": True}, {"publish_error": True}):
            rec, commands, error, _ = await self.scenario(**option)
            self.assertIsNotNone(error)
            self.assertTrue(rec["startAttempted"])
            self.assertEqual(len(commands), 1)

    async def test_ambiguous_product_rejected(self):
        m = message("g", "t", "s", TaskState.AwaitingCompletion, result())
        m.products.append(deepcopy(m.products[0]))
        with self.assertRaisesRegex(ValueError, "EXPECTED_ONE_BUSINESS_RESULT"):
            leader.LiteratureConversation.extract(m, TASK)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, *, execute=True, partner_ready=True, partner_commit="edb9dcf",
                       acl_error=False, join_timeout=False, task_timeout=False,
                       cleanup_error=False, checkpoint=None, task=None):
        operations = []
        acl = MagicMock()
        async def authorize(**kwargs):
            operations.append("acl")
            if acl_error:
                raise GroupAclError("secret-response")
        acl.add_member = AsyncMock(side_effect=authorize)
        acl.delete_group = AsyncMock()
        acl.aclose = AsyncMock()
        connection = MagicMock(is_closed=True)
        async def connect(**kwargs):
            connection.is_closed = False
        async def close():
            connection.is_closed = True
        connection.connect = AsyncMock(side_effect=connect)
        connection.close = AsyncMock(side_effect=close)
        client = MagicMock()
        client.connect = AsyncMock()
        async def create(**kwargs):
            client.group_id = kwargs["group_id"]
            return client.group_id
        client.create_group = AsyncMock(side_effect=create)
        client.start_consuming = AsyncMock()
        async def invite(*args, **kwargs):
            operations.append("invite")
            if not join_timeout:
                await client.set_message_handler.call_args.args[0](joined(client.group_id))
        client.invite_via_inbox = AsyncMock(side_effect=invite)
        async def send(command, task_id, session_id, **kwargs):
            operations.append(command.value)
            if task_timeout:
                return
            handler = client.set_message_handler.call_args.args[0]
            state = TaskState.AwaitingCompletion if command == TaskCommandType.Start else TaskState.Completed
            await handler(message(client.group_id, task_id, session_id, state,
                                  result() if command == TaskCommandType.Start else None))
        client.send_task_command = AsyncMock(side_effect=send)
        client.send_mgmt_command = AsyncMock(side_effect=RuntimeError("secret-cleanup") if cleanup_error else None)
        client.cleanup_group_resources = AsyncMock()
        cfg = config()
        from dataclasses import replace
        cfg = replace(cfg, join_timeout=.015)
        # Validate production deadlines normally; use short fixture deadline only after validation.
        real_validate = leader.validate
        def validated(c, *args):
            return real_validate(replace(c, join_timeout=1), *args)
        with ExitStack() as stack:
            stack.enter_context(patch.object(leader, "validate", side_effect=validated))
            stack.enter_context(patch.object(leader, "certificate_context"))
            a = stack.enter_context(patch.object(leader, "GroupAclClient", return_value=acl))
            b = stack.enter_context(patch.object(leader.aio_pika, "Connection", return_value=connection))
            c = stack.enter_context(patch.object(leader, "SingleTaskMqClient", return_value=client))
            deletion = stack.enter_context(patch.object(leader, "delete_resource", new_callable=AsyncMock))
            stack.enter_context(patch.object(leader, "settle_group_shutdown", new_callable=AsyncMock))
            stack.enter_context(patch("socket.getaddrinfo", side_effect=AssertionError("NO_NETWORK")))
            # Keep real config validation, only shorten the task wait for offline timeout tests.
            original_run = leader.LiteratureConversation.run
            async def quick(self, cl, t, rec, ev, timeout):
                return await original_run(self, cl, t, rec, ev, .02)
            stack.enter_context(patch.object(leader.LiteratureConversation, "run", quick))
            rec = await leader.run_group_literature(cfg, TASK if task is None else task,
                execute=execute, partner_ready=partner_ready, partner_commit=partner_commit,
                checkpoint=checkpoint)
        return rec, operations, client, (a, b, c), deletion, acl

    async def test_default_offline_no_network_or_task(self):
        rec, _, client, factories, _, _ = await self.scenario(execute=False)
        self.assertTrue(rec["passed"])
        for factory in factories:
            factory.assert_not_called()
        self.assertFalse(rec["networkAttempted"])
        client.send_task_command.assert_not_called()
        self.assertFalse(rec["protocolCompleted"])

    async def test_ready_and_version_required_before_network(self):
        for flags in ({"partner_ready": False}, {"partner_commit": None}, {"partner_commit": "invalid"}):
            rec, _, _, factories, _, _ = await self.scenario(**flags)
            self.assertFalse(rec["passed"])
            for factory in factories:
                factory.assert_not_called()

    async def test_wrong_task_rejected_before_platform(self):
        for changed in (dict(TASK, requiredSkill="literature.search"),
                        dict(TASK, constraints={"maxResults": 2}),
                        dict(TASK, outputSchema="wrong")):
            rec, _, _, factories, _, _ = await self.scenario(task=changed)
            self.assertFalse(rec["passed"])
            factories[0].assert_not_called()

    async def test_success_order_correlations_and_cleanup(self):
        rec, ops, client, _, deletion, acl = await self.scenario()
        self.assertEqual(ops, ["acl", "acl", "invite", "start", "complete"])
        self.assertTrue(rec["passed"])
        self.assertTrue(rec["cleanupSucceeded"])
        self.assertTrue(rec["connectionClosed"])
        self.assertFalse(rec["providerExecutionVerified"])
        self.assertFalse(rec["partnerCommitRemotelyVerified"])
        self.assertEqual(rec["taskCommandsSent"], 2)
        self.assertEqual(deletion.await_count, 3)
        client.send_mgmt_command.assert_awaited_once_with(GroupMgmtCommandType.DISBAND, mentions="all")
        self.assertTrue(rec["groupId"].startswith("rm-literature-"))
        self.assertEqual(acl.delete_group.call_args.kwargs["group_id"], rec["groupId"])

    async def test_acl_failure_stops_before_mq_and_redacts_error(self):
        rec, _, client, factories, _, _ = await self.scenario(acl_error=True)
        self.assertEqual(rec["error"]["code"], "GROUP_ACL_REQUEST_FAILED")
        factories[1].assert_not_called()
        client.invite_via_inbox.assert_not_called()
        self.assertNotIn("secret-response", json.dumps(rec))

    async def test_join_failure_never_starts_task(self):
        rec, _, client, _, _, _ = await self.scenario(join_timeout=True)
        self.assertEqual(rec["error"]["code"], "PARTNER_JOIN_TIMEOUT")
        client.send_task_command.assert_not_called()
        self.assertTrue(rec["cleanupSucceeded"])

    async def test_task_timeout_uncertain_no_retry_and_cleanup(self):
        rec, _, client, _, _, _ = await self.scenario(task_timeout=True)
        self.assertTrue(rec["remoteStateUnknown"])
        self.assertEqual(rec["error"]["code"], "TASK_TIMEOUT_REMOTE_STATE_UNKNOWN")
        client.send_task_command.assert_awaited_once()
        self.assertTrue(rec["cleanupSucceeded"])

    async def test_cleanup_error_preserves_business_but_prevents_pass(self):
        rec, _, _, _, _, _ = await self.scenario(cleanup_error=True)
        self.assertTrue(rec["protocolCompleted"])
        self.assertTrue(rec["businessReady"])
        self.assertFalse(rec["passed"])
        self.assertFalse(rec["cleanupSucceeded"])
        self.assertNotIn("secret-cleanup", json.dumps(rec))

    async def test_broken_checkpoint_prevents_start_and_cleanup_continues(self):
        def checkpoint(rec):
            if any(e["event"] == "TASK_START_ATTEMPT" for e in rec["events"]):
                raise OSError("secret-checkpoint")
        rec, _, client, _, _, _ = await self.scenario(checkpoint=checkpoint)
        client.send_task_command.assert_not_called()
        self.assertTrue(rec["cleanupSucceeded"])
        self.assertFalse(rec["passed"])
        self.assertNotIn("secret-checkpoint", json.dumps(rec))

    async def test_product_checkpoint_before_complete(self):
        snapshots = []
        def checkpoint(rec):
            snapshots.append(deepcopy(rec))
        rec, _, _, _, _, _ = await self.scenario(checkpoint=checkpoint)
        self.assertTrue(rec["passed"])
        saved = next(s for s in snapshots if s["events"][-1]["event"] == "BUSINESS_RESULT_VALIDATED")
        self.assertEqual(saved["result"], result())
        self.assertFalse(saved["completeAttempted"])


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def client(self):
        connection, channel, exchange = MagicMock(is_closed=False), MagicMock(is_closed=False), MagicMock()
        exchange.publish = AsyncMock()
        channel.declare_exchange = AsyncMock(return_value=exchange)
        queue = MagicMock()
        queue.bind = AsyncMock()
        channel.declare_queue = AsyncMock(return_value=queue)
        connection.channel = AsyncMock(return_value=channel)
        client = leader.SingleTaskMqClient(leader_aic=LEADER, connection=connection,
                                          connection_owner=False, identity_binding_enabled=True)
        await client.connect()
        await client.create_group(group_id="rm-literature-offline", partner_acs_list=[])
        client.bind_task(task_id="t", session_id="s", partner_aic=PARTNER, payload=TASK)
        return client, exchange

    async def test_real_sdk_serializes_structured_start_and_mtls_bound_user_id(self):
        client, exchange = await self.client()
        client.task_sending_enabled = True
        await client.send_task_command(TaskCommandType.Start, "t", "s",
            data_items=[StructuredDataItem(data=TASK)], mentions=[PARTNER])
        wire = exchange.publish.call_args.args[0]
        payload = json.loads(wire.body)
        self.assertEqual(wire.user_id, LEADER)
        self.assertEqual(payload["type"], "task-command")
        self.assertEqual(payload["groupId"], "rm-literature-offline")
        self.assertEqual(payload["mentions"], [PARTNER])
        self.assertEqual(payload["dataItems"][0]["data"], TASK)
        await client.send_task_command(TaskCommandType.Complete, "t", "s", mentions=[PARTNER])
        self.assertEqual(exchange.publish.await_count, 2)
        with self.assertRaisesRegex(ValueError, "RETRY_DISABLED"):
            await client.send_task_command(TaskCommandType.Start, "t", "s",
                data_items=[StructuredDataItem(data=TASK)], mentions=[PARTNER])

    async def test_scope_and_prejoin_send_guard(self):
        client, exchange = await self.client()
        with self.assertRaisesRegex(ValueError, "NOT_ARMED"):
            await client.send_task_command(TaskCommandType.Start, "t", "s", mentions=[PARTNER])
        client.task_sending_enabled = True
        for command, task, mentions in ((TaskCommandType.Start, "wrong", [PARTNER]),
                                         (TaskCommandType.Start, "t", "all"),
                                         (TaskCommandType.Cancel, "t", [PARTNER]),
                                         (TaskCommandType.Get, "t", [PARTNER])):
            with self.assertRaisesRegex(ValueError, "SCOPE_MISMATCH"):
                await client.send_task_command(command, task, "s", mentions=mentions)
        with self.assertRaisesRegex(ValueError, "EXACT_STRUCTURED"):
            await client.send_task_command(TaskCommandType.Start, "t", "s", text_content="LoRA", mentions=[PARTNER])
        exchange.publish.assert_not_called()

    async def test_publish_failure_cannot_retry_start(self):
        client, exchange = await self.client()
        client.task_sending_enabled = True
        exchange.publish.side_effect = RuntimeError("publish-failed")
        for expected in (RuntimeError, ValueError):
            with self.assertRaises(expected):
                await client.send_task_command(TaskCommandType.Start, "t", "s",
                    data_items=[StructuredDataItem(data=TASK)], mentions=[PARTNER])
        exchange.publish.assert_awaited_once()

    async def test_complete_cannot_precede_start(self):
        client, exchange = await self.client()
        client.task_sending_enabled = True
        with self.assertRaisesRegex(ValueError, "COMPLETE_BEFORE_START"):
            await client.send_task_command(TaskCommandType.Complete, "t", "s", mentions=[PARTNER])
        exchange.publish.assert_not_called()

    async def test_sdk_rejects_spoofed_sender_before_callback(self):
        m = message("g", "t", "s", TaskState.Working)
        with self.assertRaises(SenderIdentityMismatchError):
            assert_incoming_group_message_identity(m, amqp_user_id=LEADER, expected_group_id="g")


class ReportTests(unittest.TestCase):
    def test_failure_diagnostic_keeps_code_but_not_remote_secrets(self):
        m = message("g", "t", "s", TaskState.Failed)
        m.status.dataItems = [StructuredDataItem(data={
            "error": {"code": "RATE_LIMITED", "message": "Bearer secret-token"},
            "password": "secret-password"})]
        diagnostic = leader.partner_failure_diagnostic(m)
        self.assertEqual(diagnostic["codes"], ["RATE_LIMITED"])
        self.assertNotIn("secret", json.dumps(diagnostic))
        self.assertFalse(diagnostic["requiresPartnerLog"])

    def test_unknown_remote_error_is_not_copied(self):
        m = message("g", "t", "s", TaskState.Failed)
        m.dataItems = [StructuredDataItem(data={"code": "secret-token", "error": ["unsafe"]})]
        diagnostic = leader.partner_failure_diagnostic(m)
        self.assertEqual(diagnostic["codes"], [])
        self.assertTrue(diagnostic["requiresPartnerLog"])
        self.assertNotIn("secret", json.dumps(diagnostic))

    def test_live_report_distinguishes_automatic_and_joint_acceptance(self):
        record = {"scope": "PLATFORM_LITERATURE_SINGLE_TASK", "groupId": "g",
                  "taskId": "t", "sessionId": "s", "protocolCompleted": False,
                  "businessReady": False, "cleanupSucceeded": True, "passed": False,
                  "error": {"code": "PARTNER_FAILED"}, "remoteStateUnknown": False,
                  "result": None, "partnerFailure": {"codes": ["RATE_LIMITED"]}}
        report = cli.report_text(record)
        self.assertIn("RATE_LIMITED", report)
        self.assertIn("验收待人工核对", report)
        self.assertIn("不代表已收到 Partner 离群确认", report)

    def test_example_seven_fields_and_max_one(self):
        task = json.loads((Path(leader.__file__).parent /
                          "examples/group_literature_single_task_v02.json").read_text(encoding="utf-8"))
        self.assertEqual(len(leader.validate_literature_task(task)), 7)

    def test_offline_cli_writes_truthful_report_without_live_flags(self):
        async def offline(config, task, **kwargs):
            self.assertFalse(kwargs["execute"])
            self.assertFalse(kwargs["partner_ready"])
            rec = {"scope": "OFFLINE_PLATFORM_LITERATURE_PREFLIGHT", "groupId": "fixture",
                   "taskId": "fixture", "sessionId": "fixture", "events": [], "result": None,
                   "passed": True, "protocolCompleted": False, "businessReady": False,
                   "cleanupSucceeded": None, "error": None, "remoteStateUnknown": False}
            kwargs["checkpoint"](rec)
            return rec
        task_path = Path(leader.__file__).parent / "examples/group_literature_single_task_v02.json"
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "ROOT", Path(directory)), \
                patch.object(cli, "run_group_literature", side_effect=offline), patch("builtins.print"):
            status = cli.main(["--leader-aic", LEADER, "--partner-aic", PARTNER,
                               "--credentials-dir", directory, "--task", str(task_path)])
            self.assertEqual(status, 0)
            reports = list(Path(directory).glob("outputs/platform-literature/*/report.md"))
            self.assertEqual(len(reports), 1)
            text = reports[0].read_text(encoding="utf-8")
            self.assertIn("离线预检，未发送任务", text)
            self.assertIn("完成确认：False", text)
            self.assertIn("未使用 ADP", text)

    def test_invalid_task_does_not_create_platform(self):
        with patch.object(cli, "run_group_literature") as runner:
            status = cli.main(["--leader-aic", LEADER, "--partner-aic", PARTNER,
                               "--credentials-dir", tempfile.gettempdir(), "--task", "not-present-task.json"])
        self.assertEqual(status, 2)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
