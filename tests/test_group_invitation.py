"""Offline workflows, official SDK wire/identity checks, and no-task safeguards."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from aiormq.exceptions import ChannelNotFoundEntity
from acps_sdk.aip import ACSObject, GroupMgmtCommand, GroupMgmtCommandType, GroupMgmtResult, StructuredDataItem, TaskCommand, TaskCommandType
from acps_sdk.aip.aip_group_auth import GroupAclClient, GroupAclError
from acps_sdk.aip.aip_group_identity import assert_incoming_group_message_identity
from acps_sdk.aip.aip_identity import SenderIdentityMismatchError
from acps_sdk.aip.aip_group_runtime import build_group_exchange_name, build_group_queue_name

import group_invitation as invitation
import run_group_invitation as cli
from platform_mq_check import ROOT, CheckError

# Synthetic, format-valid fixture identities. They are not registered platform AICs.
LEADER = "1.2.156.3088.1.TEST.AAAAAA.BBBBBB.CCCCCC.0000"
PARTNER = "1.2.156.3088.1.TEST.AAAAAA.BBBBBB.DDDDDD.0000"


def config():
    return invitation.InviteConfig(LEADER, PARTNER, Path(tempfile.gettempdir()), hold_seconds=0)


def joined(group_id, *, partner=PARTNER, connected=True, role="partner"):
    return GroupMgmtResult(id="offline-join-message", sentAt=datetime.now(timezone.utc).isoformat(),
                           senderRole=role, senderId=partner, groupId=group_id,
                           status={"connected": connected, "muted": False})


class ValidationTests(unittest.TestCase):
    def test_real_modes_require_explicit_execute(self):
        for mode in ("acl-check", "invite"):
            with self.subTest(mode=mode), self.assertRaisesRegex(CheckError, "REQUIRES_EXECUTE"):
                invitation.validate(config(), mode, False, True)

    def test_offline_cannot_use_execute(self):
        with self.assertRaisesRegex(CheckError, "OFFLINE_MODE"):
            invitation.validate(config(), "offline", True, False)

    def test_invite_requires_current_partner_ready_confirmation(self):
        with self.assertRaisesRegex(CheckError, "PARTNER_READY"):
            invitation.validate(config(), "invite", True, False)

    def test_untrusted_urls_rejected(self):
        for url in ("http://wt.ioa.pub/mq-auth/", "https://localhost:9007", "https://example.invalid/mq-auth/",
                    "https://wt.ioa.pub:9007", "https://wt.ioa.pub/mq-auth/?x=1",
                    "https://user:password@wt.ioa.pub/mq-auth/", "https://wt.ioa.pub/mq-auth/#x"):
            with self.subTest(url=url), self.assertRaisesRegex(CheckError, "UNCONFIRMED_AUTH"):
                invitation.validate(replace(config(), auth_service_url=url), "offline", False, False)

    def test_confirmed_url_accepts_optional_trailing_slash(self):
        invitation.validate(replace(config(), auth_service_url=invitation.AUTH_SERVICE_URL.rstrip("/")), "offline", False, False)

    def test_identity_format_and_self_invite_rejected(self):
        for bad in ("{AIC}", "\x16", LEADER):
            with self.subTest(bad=bad), self.assertRaises(CheckError):
                invitation.validate(replace(config(), partner_aic=bad), "offline", False, False)

    def test_credentials_cannot_be_in_checkout(self):
        with self.assertRaisesRegex(CheckError, "OUTSIDE_REPOSITORY"):
            invitation.validate(replace(config(), credentials_dir=ROOT / "outputs"), "offline", False, False)

    def test_invalid_wait_settings_rejected(self):
        for field, value in (("join_timeout", 0), ("join_timeout", 121), ("hold_seconds", -1),
                             ("hold_seconds", 61), ("hold_seconds", float("nan"))):
            with self.subTest(field=field, value=value), self.assertRaises(CheckError):
                invitation.validate(replace(config(), **{field: value}), "offline", False, False)


class JoinTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_target_connected_status_accepted(self):
        monitor = invitation.JoinConfirmation(PARTNER, "rm-invite-offline")
        for message in (joined("other-group"), joined(monitor.group_id, partner=LEADER),
                        joined(monitor.group_id, connected=False), joined(monitor.group_id, role="leader")):
            await monitor.on_message(message)
            self.assertFalse(monitor.event.is_set())
        await monitor.on_message(joined(monitor.group_id))
        self.assertTrue(monitor.event.is_set())

    async def test_task_message_is_not_executed_or_counted_as_join(self):
        monitor = invitation.JoinConfirmation(PARTNER, "rm-invite-offline")
        message = TaskCommand(id="offline-task", sentAt="2026-10-18T00:00:00Z", senderRole="leader",
                              senderId=LEADER, groupId=monitor.group_id, taskId="test", command=TaskCommandType.Start)
        await monitor.on_message(message)
        self.assertFalse(monitor.event.is_set())
        self.assertEqual(monitor.unexpected_task_messages, 1)

    async def test_sdk_identity_binding_rejects_spoofing(self):
        message = joined("rm-invite-offline")
        with self.assertRaises(SenderIdentityMismatchError):
            assert_incoming_group_message_identity(message, amqp_user_id=LEADER,
                                                    expected_group_id=message.groupId)

    async def test_sdk_identity_binding_rejects_wrong_group(self):
        with self.assertRaises(SenderIdentityMismatchError):
            assert_incoming_group_message_identity(joined("wrong-group"), amqp_user_id=PARTNER,
                                                    expected_group_id="rm-invite-offline")

    async def test_all_task_commands_blocked_at_send_boundary(self):
        client = invitation.InvitationOnlyMqClient(leader_aic=LEADER)
        for command in TaskCommandType:
            with self.subTest(command=command), self.assertRaisesRegex(CheckError, "COMMANDS_DISABLED"):
                await client.publish_message(TaskCommand(id="test", sentAt="2026-10-18T00:00:00Z",
                    senderRole="leader", senderId=LEADER, taskId="test", command=command))

    async def test_other_management_commands_not_exposed(self):
        client = invitation.InvitationOnlyMqClient(leader_aic=LEADER)
        with self.assertRaisesRegex(CheckError, "COMMANDS_DISABLED"):
            await client.publish_message(GroupMgmtCommand(id="test", sentAt="2026-10-18T00:00:00Z",
                senderRole="leader", senderId=LEADER, groupId="rm-invite-offline", command=GroupMgmtCommandType.GET_STATUS))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, *, mode="invite", leader_error=None, partner_error=None,
                       group_error=None, publish_error=None, timeout=False, interrupt=False,
                       cleanup_error=None, resource_error=None, checkpoint=None, no_ack=False, consumer_error=False):
        operations = []
        acl = MagicMock()
        async def add_member(**kwargs):
            operations.append(("authorize", kwargs["member_aic"]))
            error = leader_error if kwargs["member_aic"] == LEADER else partner_error
            if error:
                raise error
        async def delete_group(**kwargs):
            operations.append(("revoke", kwargs["group_id"]))
            if cleanup_error:
                raise cleanup_error
        acl.add_member = AsyncMock(side_effect=add_member)
        acl.delete_group = AsyncMock(side_effect=delete_group)
        acl.aclose = AsyncMock()
        connection = MagicMock(is_closed=True)
        async def connect(**kwargs):
            operations.append(("connect", None))
            connection.is_closed = False
        async def close():
            connection.is_closed = True
        connection.connect = AsyncMock(side_effect=connect)
        connection.close = AsyncMock(side_effect=close)
        client = MagicMock()
        client.connect = AsyncMock()
        async def create_group(**kwargs):
            client.group_id = kwargs["group_id"]
            operations.append(("create", client.group_id))
            if group_error:
                raise group_error
            return client.group_id
        client.create_group = AsyncMock(side_effect=create_group)
        client.start_consuming = AsyncMock()
        async def publish(*args, **kwargs):
            operations.append(("invite", args[0].aic))
            if publish_error:
                raise publish_error
            if interrupt:
                raise asyncio.CancelledError()
            if not timeout:
                handler = client.set_message_handler.call_args.args[0]
                await handler(joined(client.group_id))
        client.invite_via_inbox = AsyncMock(side_effect=publish)
        client.broadcast_group_shutdown = AsyncMock()
        client.cleanup_group_resources = AsyncMock()
        client._build_mgmt_command.side_effect = lambda **kw: GroupMgmtCommand(
            id="cleanup-command", sentAt=datetime.now(timezone.utc).isoformat(),
            senderId=LEADER, senderRole="leader", groupId=client.group_id,
            sessionId=kw["session_id"], command=kw["command"], mentions=kw["mentions"])
        async def publish_disband(command):
            if no_ack:
                return
            await client.set_message_handler.call_args.args[0](GroupMgmtResult(
                id="leave-receipt", sentAt=datetime.now(timezone.utc).isoformat(),
                senderId=PARTNER, senderRole="partner", groupId=command.groupId,
                sessionId=command.sessionId, status={"connected": False, "muted": False},
                dataItems=[StructuredDataItem(data={"event": "GROUP_LEAVE_COMPLETED", "disbandCommandId": command.id})]))
        client.publish_message = AsyncMock(side_effect=publish_disband)
        with ExitStack() as stack:
            stack.enter_context(patch("group_literature_leader.stop_local_group_consumer", new_callable=AsyncMock,
                side_effect=RuntimeError("cancel failed") if consumer_error else None))
            stack.enter_context(patch("group_invitation.certificate_context"))
            acl_factory = stack.enter_context(patch("group_invitation.GroupAclClient", return_value=acl))
            conn_factory = stack.enter_context(patch("group_invitation.aio_pika.Connection", return_value=connection))
            client_factory = stack.enter_context(patch("group_invitation.InvitationOnlyMqClient", return_value=client))
            deletion = stack.enter_context(patch("group_invitation.delete_resource", new_callable=AsyncMock,
                                                side_effect=resource_error))
            stack.enter_context(patch("socket.getaddrinfo", side_effect=AssertionError("NO_REAL_NETWORK")))
            result = await invitation.run_invitation(replace(config(), timeout=1) if no_ack else config(), mode=mode, execute=mode != "offline",
                                                     partner_ready=True, checkpoint=checkpoint)
        return result, acl, connection, client, deletion, operations, (acl_factory, conn_factory, client_factory)

    async def test_offline_opens_no_network_clients(self):
        result, _, _, _, _, _, factories = await self.scenario(mode="offline")
        for factory in factories:
            factory.assert_not_called()
        self.assertTrue(result["passed"])
        self.assertFalse(result["networkAttempted"])

    async def test_acl_mode_authorizes_only_leader_then_deletes_own_group(self):
        result, acl, connection, client, _, _, factories = await self.scenario(mode="acl-check")
        self.assertTrue(result["passed"])
        self.assertTrue(result["aclDeleted"])
        acl.add_member.assert_awaited_once()
        self.assertEqual(acl.add_member.call_args.kwargs["member_aic"], LEADER)
        self.assertEqual(acl.delete_group.call_args.kwargs["group_id"], result["groupId"])
        self.assertTrue(result["groupId"].startswith("rm-acl-check-"))
        factories[1].assert_not_called()
        factories[2].assert_not_called()
        self.assertFalse(result["groupCreated"])
        self.assertFalse(result["invitationPublishAttempted"])

    async def test_success_order_single_invite_and_no_tasks(self):
        result, acl, connection, client, deletion, operations, _ = await self.scenario()
        self.assertTrue(result["passed"])
        self.assertTrue(result["partnerJoinConfirmed"])
        self.assertTrue(result["cleanupSucceeded"])
        self.assertTrue(result["connectionClosed"])
        self.assertEqual([entry[0] for entry in operations], ["authorize", "connect", "create", "authorize", "invite", "revoke"])
        client.invite_via_inbox.assert_awaited_once()
        client.start_task.assert_not_called()
        client.send_task_command.assert_not_called()
        self.assertEqual(result["taskCommandsSent"], 0)
        self.assertFalse(result["protocolCompleted"])
        self.assertFalse(result["businessReady"])
        self.assertEqual(deletion.await_count, 3)
        self.assertEqual({args.args[2] for args in deletion.await_args_list}, {
            build_group_queue_name(LEADER, result["groupId"], PARTNER),
            build_group_queue_name(LEADER, result["groupId"], LEADER),
            build_group_exchange_name(LEADER, result["groupId"]),
        })

    async def test_acl_rejection_stops_before_mq_and_invite(self):
        result, _, _, client, _, _, factories = await self.scenario(leader_error=GroupAclError("403 secret-value"))
        self.assertEqual(result["error"]["code"], "GROUP_ACL_REQUEST_FAILED")
        factories[1].assert_not_called()
        client.invite_via_inbox.assert_not_called()
        self.assertNotIn("secret-value", str(result))

    async def test_partner_acl_rejection_stops_before_invite_and_cleans(self):
        result, _, connection, client, _, _, _ = await self.scenario(partner_error=GroupAclError("403"))
        self.assertFalse(result["passed"])
        self.assertTrue(result["cleanupSucceeded"])
        client.invite_via_inbox.assert_not_called()
        connection.close.assert_awaited_once()

    async def test_partial_group_creation_preserves_uncertain_resources(self):
        result, acl, _, client, deletion, _, _ = await self.scenario(group_error=TimeoutError())
        self.assertFalse(result["groupCreated"])
        self.assertTrue(result["groupCreationAttempted"])
        deletion.assert_not_awaited()
        acl.delete_group.assert_not_awaited()
        client.invite_via_inbox.assert_not_called()

    async def test_publish_failure_is_not_retried(self):
        result, _, _, client, _, _, _ = await self.scenario(publish_error=RuntimeError("secret-value"))
        client.invite_via_inbox.assert_awaited_once()
        self.assertTrue(result["invitationPublishAttempted"])
        self.assertFalse(result["invitationPublished"])
        self.assertTrue(result["cleanupSucceeded"])
        self.assertNotIn("secret-value", str(result))

    async def test_interruption_still_revokes_and_closes(self):
        result, acl, connection, _, _, _, _ = await self.scenario(interrupt=True)
        self.assertEqual(result["error"]["code"], "INTERRUPTED")
        acl.delete_group.assert_awaited_once()
        connection.close.assert_awaited_once()

    async def test_receipt_or_consumer_failure_prevents_all_deletions(self):
        for options in ({"no_ack": True}, {"consumer_error": True}):
            result, acl, _, client, deletion, _, _ = await self.scenario(**options)
            self.assertFalse(result["cleanupSucceeded"])
            self.assertFalse(result["aclDeleted"])
            deletion.assert_not_awaited()
            acl.delete_group.assert_not_awaited()
            client.cleanup_group_resources.assert_not_awaited()

    async def test_first_resource_failure_stops_subsequent_deletions(self):
        result, acl, _, _, deletion, _, _ = await self.scenario(resource_error=PermissionError())
        self.assertEqual(deletion.await_count, 1)
        acl.delete_group.assert_not_awaited()
        self.assertFalse(result["cleanupSucceeded"])

    async def test_cleanup_failure_cannot_claim_pass(self):
        result, _, _, _, _, _, _ = await self.scenario(cleanup_error=RuntimeError("secret-value"))
        self.assertTrue(result["partnerJoinConfirmed"])
        self.assertFalse(result["cleanupSucceeded"])
        self.assertFalse(result["passed"])
        self.assertNotIn("secret-value", str(result))

    async def test_resource_deletion_failure_is_preserved(self):
        result, _, _, _, _, _, _ = await self.scenario(resource_error=PermissionError())
        self.assertFalse(result["groupResourcesDeleted"])
        self.assertFalse(result["passed"])

    async def test_journal_contains_group_before_external_mutation(self):
        events = []
        def checkpoint(record):
            events.append((record["events"][-1]["event"], record["groupId"]))
        result, _, _, _, _, _, _ = await self.scenario(checkpoint=checkpoint)
        self.assertIn(("LEADER_ACL_WRITE_ATTEMPT", result["groupId"]), events)
        self.assertIn(("INVITATION_PUBLISH_ATTEMPT", result["groupId"]), events)

    async def test_broken_journal_cannot_prevent_cleanup(self):
        def checkpoint(record):
            if record["aclWriteAttempted"]:
                raise OSError("secret-value")
        result, acl, _, client, _, _, _ = await self.scenario(checkpoint=checkpoint)
        self.assertFalse(result["passed"])
        self.assertTrue(result["cleanupAttempted"])
        acl.delete_group.assert_awaited_once()
        client.invite_via_inbox.assert_not_called()

    async def test_join_deadline_and_offline_target_are_not_success(self):
        original = asyncio.wait_for
        with patch("group_invitation.asyncio.wait_for", wraps=asyncio.wait_for) as wait:
            # Only force timeout for the join Event wait; cleanup must still run.
            async def deadline(awaitable, *, timeout):
                if timeout == config().join_timeout:
                    awaitable.close()
                    raise TimeoutError()
                return await original(awaitable, timeout=timeout)
            wait.side_effect = deadline
            result, _, _, _, _, _, _ = await self.scenario(timeout=True)
        self.assertEqual(result["error"]["code"], "PARTNER_JOIN_TIMEOUT")
        self.assertFalse(result["passed"])
        self.assertTrue(result["cleanupSucceeded"])


class SdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_acl_sdk_preserves_mq_auth_prefix_and_methods(self):
        seen = []
        def transport(request):
            seen.append((request.method, request.url.path))
            return httpx.Response(204)
        http = httpx.AsyncClient(base_url=invitation.AUTH_SERVICE_URL, transport=httpx.MockTransport(transport))
        with patch("acps_sdk.aip.aip_group_auth.httpx.AsyncClient", return_value=http):
            acl = GroupAclClient(base_url=invitation.AUTH_SERVICE_URL)
        await acl.add_member(leader_aic=LEADER, group_id="rm-invite-offline", member_aic=PARTNER)
        await acl.delete_group(leader_aic=LEADER, group_id="rm-invite-offline")
        await acl.aclose()
        self.assertEqual(seen, [
            ("PUT", f"/mq-auth/groups/{LEADER}/rm-invite-offline/members/{PARTNER}"),
            ("DELETE", f"/mq-auth/groups/{LEADER}/rm-invite-offline"),
        ])

    async def test_official_sdk_serializes_one_invitation_not_task(self):
        client = invitation.InvitationOnlyMqClient(leader_aic=LEADER)
        connection = MagicMock(is_closed=False)
        channel = MagicMock(is_closed=False)
        group_exchange = MagicMock()
        group_exchange.publish = AsyncMock()
        inbox_exchange = MagicMock()
        inbox_exchange.publish = AsyncMock()
        async def exchange(**kwargs):
            return inbox_exchange if kwargs["name"] == "inbox.topic" else group_exchange
        channel.declare_exchange = AsyncMock(side_effect=exchange)
        queue = MagicMock()
        queue.bind = AsyncMock()
        channel.declare_queue = AsyncMock(return_value=queue)
        connection.channel = AsyncMock(return_value=channel)
        # Constructor injection is the public SDK API, not upstream modification.
        client = invitation.InvitationOnlyMqClient(leader_aic=LEADER, connection=connection,
                                                   connection_owner=False, identity_binding_enabled=True)
        await client.connect()
        await client.create_group(group_id="rm-invite-offline", partner_acs_list=[])
        await client.invite_via_inbox(ACSObject(aic=PARTNER), partner_inbox=f"inbox_{PARTNER}", timeout_seconds=45)
        inbox_exchange.publish.assert_awaited_once()
        message = inbox_exchange.publish.call_args.args[0]
        payload = json.loads(message.body)
        self.assertEqual(payload["type"], "group-invitation")
        self.assertEqual(payload["senderId"], LEADER)
        self.assertEqual(payload["groupId"], "rm-invite-offline")
        self.assertEqual(message.user_id, LEADER)
        self.assertEqual(inbox_exchange.publish.call_args.kwargs["routing_key"], f"inbox_{PARTNER}")
        group_exchange.publish.assert_not_called()

    async def test_cleanup_absent_queue_is_success(self):
        channel = MagicMock(is_closed=False)
        channel.queue_delete = AsyncMock(side_effect=ChannelNotFoundEntity())
        channel.close = AsyncMock()
        connection = MagicMock()
        connection.channel = AsyncMock(return_value=channel)
        await invitation.delete_resource(connection, "queue", "rm-invite-offline-queue")
        channel.close.assert_awaited_once()

    async def test_cleanup_access_failure_is_not_swallowed(self):
        channel = MagicMock(is_closed=False)
        channel.queue_delete = AsyncMock(side_effect=PermissionError())
        channel.close = AsyncMock()
        connection = MagicMock()
        connection.channel = AsyncMock(return_value=channel)
        with self.assertRaises(PermissionError):
            await invitation.delete_resource(connection, "queue", "rm-invite-offline-queue")


class CliTests(unittest.TestCase):
    def test_default_mode_is_offline_and_journal_is_created(self):
        async def fake(config, **kwargs):
            self.assertEqual(kwargs["mode"], "offline")
            self.assertFalse(kwargs["execute"])
            result = {"groupId": "rm-invite-offline", "events": [], "passed": True}
            kwargs["checkpoint"](result)
            return result
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "run_invitation", side_effect=fake), patch("builtins.print"):
            status = cli.main(["--leader-aic", LEADER, "--partner-aic", PARTNER,
                               "--credentials-dir", directory, "--output-dir", directory])
            self.assertEqual(status, 0)
            self.assertEqual(len(list(Path(directory).glob("*/record.json"))), 1)


if __name__ == "__main__":
    unittest.main()
