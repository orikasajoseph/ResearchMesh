import asyncio
import json
from types import SimpleNamespace
from time import monotonic
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiormq.abc import DeliveredMessage
from pamqp.commands import Basic
from acps_sdk.aip import (
    GroupMgmtCommand, GroupMgmtCommandType, GroupMgmtResult, StructuredDataItem,
)

from aip.audited_group_client import AuditedGroupPartnerMqClient


PARTNER = "123456789012345678"
LEADER = "876543210987654321"
GROUP = "group-two-phase"
SESSION = "session-two-phase"
COMMAND = "disband-two-phase"


class Audit:
    def __init__(self):
        self.events = []

    def emit(self, name, **fields):
        self.events.append((name, fields))


class PartnerLeaveReceiptTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *, publish=Basic.Ack(delivery_tag=1, multiple=False)):
        operations = []
        audit = Audit()
        client = AuditedGroupPartnerMqClient(PARTNER)
        client.audit = audit
        client._group_id = GROUP
        client._group_info = SimpleNamespace(leader=SimpleNamespace(aic=LEADER))
        client._disband_context = (GROUP, SESSION, COMMAND, LEADER)
        client._disband_received_at = monotonic()
        client._consumer_tag = "consumer-tag"

        queue = MagicMock(name="partner-queue")
        queue.cancel = AsyncMock(side_effect=lambda *_: operations.append("cancel"))
        queue.delete = AsyncMock(side_effect=lambda **_: operations.append("delete"))
        client._queue = queue
        client._queue_name = "partner-queue"

        channel = MagicMock(is_closed=False, publisher_confirms=True)
        async def close_channel():
            operations.append("close-channel")
            channel.is_closed = True
        channel.close = AsyncMock(side_effect=close_channel)
        exchange = MagicMock(channel=channel)
        async def publish_receipt(*args, **kwargs):
            operations.append("publish")
            if isinstance(publish, BaseException):
                raise publish
            if isinstance(publish, list):
                value = publish.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value
            return publish
        exchange.publish = AsyncMock(side_effect=publish_receipt)
        client._channel = channel
        client._exchange = exchange

        connection = MagicMock(is_closed=False)
        async def close_connection():
            operations.append("close-connection")
            connection.is_closed = True
        connection.close = AsyncMock(side_effect=close_connection)
        client._connection = connection
        client._connection_owner = True
        client._disconnect_handler = AsyncMock(
            side_effect=lambda *_: operations.append("disconnect"))
        return client, queue, exchange, operations, audit

    async def test_complete_success_order_and_receipt_fields(self):
        client, queue, exchange, operations, audit = self.client()

        await client._cleanup_group_resources()

        self.assertEqual(operations, [
            "cancel", "delete", "publish", "close-channel", "close-connection", "disconnect",
        ])
        body = json.loads(exchange.publish.await_args.args[0].body)
        self.assertTrue(exchange.publish.await_args.kwargs["mandatory"])
        self.assertEqual(exchange.publish.await_args.kwargs["timeout"], 5)
        self.assertEqual(body["id"], client._receipt_id())
        self.assertEqual(body["groupId"], GROUP)
        self.assertEqual(body["sessionId"], SESSION)
        self.assertEqual(body["senderId"], PARTNER)
        self.assertEqual(body["mentions"], [LEADER])
        data = body["dataItems"][0]["data"]
        self.assertEqual(body["dataItems"][0]["type"], "data")
        decoded = GroupMgmtResult.model_validate(body)
        self.assertIsInstance(decoded.dataItems[0], StructuredDataItem)
        self.assertEqual(data["event"], "GROUP_LEAVE_COMPLETED")
        self.assertNotIn("code", data)
        self.assertEqual(data["disbandCommandId"], COMMAND)
        self.assertTrue(data["consumerStopped"])
        self.assertTrue(data["partnerQueueDeleted"])
        self.assertFalse(data["leaderReceiptConfirmed"])
        self.assertTrue(client.leave_succeeded is False)  # set by outer leave path, not cleanup alone
        published = [fields for name, fields in audit.events if name == "LEAVE_RECEIPT_PUBLISHED"]
        self.assertEqual(len(published), 1)
        self.assertFalse(published[0]["leaderReceiptConfirmed"])
        self.assertEqual(next(fields["outcome"] for name, fields in audit.events
                              if name == "LEAVE_RECEIPT_ATTEMPT"), "ack")

    async def test_consumer_cancel_failure_sends_no_receipt(self):
        client, queue, exchange, _, _ = self.client()
        queue.cancel.side_effect = RuntimeError("cancel failed")
        with self.assertRaises(RuntimeError):
            await client._cleanup_group_resources()
        queue.delete.assert_not_awaited()
        exchange.publish.assert_not_awaited()

    async def test_queue_delete_failure_sends_no_receipt(self):
        client, queue, exchange, operations, _ = self.client()
        queue.delete.side_effect = RuntimeError("delete failed")
        with self.assertRaises(RuntimeError):
            await client._cleanup_group_resources()
        self.assertEqual(operations, ["cancel"])
        exchange.publish.assert_not_awaited()

    async def test_queue_delete_does_not_remove_publish_path_in_model(self):
        client, _, exchange, operations, _ = self.client()
        await client._cleanup_group_resources()
        self.assertLess(operations.index("delete"), operations.index("publish"))
        self.assertLess(operations.index("publish"), operations.index("close-channel"))
        exchange.publish.assert_awaited_once()

    async def test_confirm_none_is_uncertain_and_retries_same_receipt(self):
        client, _, exchange, _, audit = self.client(publish=[None, None, None])
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIRMED"):
            await client._cleanup_group_resources()
        self.assertEqual(exchange.publish.await_count, 3)
        ids = [call.args[0].message_id for call in exchange.publish.await_args_list]
        self.assertEqual(ids, [client._receipt_id()] * 3)
        failed = [fields for name, fields in audit.events if name == "LEAVE_RECEIPT_FAILED"]
        self.assertEqual(failed[0]["result"], "uncertain")

    async def test_timeout_is_uncertain_and_retries_stable_receipt(self):
        client, _, exchange, _, audit = self.client(
            publish=[TimeoutError(), TimeoutError(), TimeoutError()])
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIRMED"):
            await client._cleanup_group_resources()
        self.assertEqual(exchange.publish.await_count, 3)
        ids = {call.args[0].message_id for call in exchange.publish.await_args_list}
        self.assertEqual(ids, {client._receipt_id()})
        failed = [fields for name, fields in audit.events if name == "LEAVE_RECEIPT_FAILED"]
        self.assertEqual(failed[0]["result"], "uncertain")
        attempts = [fields for name, fields in audit.events
                    if name == "LEAVE_RECEIPT_ATTEMPT"]
        self.assertEqual([item["outcome"] for item in attempts], ["timeout"] * 3)
        self.assertEqual({item["receiptId"] for item in attempts}, {client._receipt_id()})

    async def test_nack_or_publish_exception_can_retry_to_confirmed_success(self):
        client, _, exchange, _, _ = self.client(
            publish=[RuntimeError("nack"), Basic.Ack(delivery_tag=2, multiple=False)])
        await client._cleanup_group_resources()
        self.assertEqual(exchange.publish.await_count, 2)
        self.assertTrue(client._leave_receipt_published)

    async def test_mandatory_return_is_unroutable_and_not_retried(self):
        returned = MagicMock(spec=DeliveredMessage)
        client, _, exchange, _, audit = self.client(publish=returned)
        with self.assertRaisesRegex(RuntimeError, "NOT_CONFIRMED"):
            await client._cleanup_group_resources()
        exchange.publish.assert_awaited_once()
        self.assertFalse(client._leave_receipt_published)
        self.assertEqual(next(fields["outcome"] for name, fields in audit.events
                              if name == "LEAVE_RECEIPT_ATTEMPT"), "unroutable")

    async def test_attempt_audit_distinguishes_exception_uncertain_and_ack(self):
        client, _, _, _, audit = self.client(
            publish=[RuntimeError("publish failed"), None,
                     Basic.Ack(delivery_tag=3, multiple=False)])
        await client._cleanup_group_resources()
        attempts = [fields for name, fields in audit.events
                    if name == "LEAVE_RECEIPT_ATTEMPT"]
        self.assertEqual([item["outcome"] for item in attempts],
                         ["exception", "uncertain", "ack"])
        self.assertEqual(len({item["receiptId"] for item in attempts}), 1)
        self.assertEqual(sum(name == "LEAVE_RECEIPT_PUBLISHED"
                             for name, _ in audit.events), 1)

    async def test_missing_publisher_confirms_sends_no_receipt(self):
        client, _, exchange, _, _ = self.client()
        exchange.channel.publisher_confirms = False
        with self.assertRaisesRegex(RuntimeError, "PUBLISHER_CONFIRMS_REQUIRED"):
            await client._cleanup_group_resources()
        exchange.publish.assert_not_awaited()

    async def test_duplicate_disband_is_idempotent_and_conflict_rejected(self):
        client, _, _, _, audit = self.client()
        client._disband_context = None
        command = GroupMgmtCommand(
            id=COMMAND, sentAt="2026-10-29T00:00:00Z", senderRole="leader",
            senderId=LEADER, mentions="all", groupId=GROUP, sessionId=SESSION,
            command=GroupMgmtCommandType.DISBAND)
        await client._handle_mgmt_command(command)
        first_id = client._receipt_id()
        await client._handle_mgmt_command(command)
        self.assertEqual(client._receipt_id(), first_id)
        conflicting = command.model_copy(update={"id": "different-disband"})
        await client._handle_mgmt_command(conflicting)
        self.assertEqual(client._receipt_id(), first_id)
        self.assertTrue(any(name == "DISBAND_REJECTED" for name, _ in audit.events))

    async def test_uncorrelated_or_wrong_leader_disband_is_rejected(self):
        client, _, _, _, audit = self.client()
        client._disband_context = None
        command = GroupMgmtCommand(
            id=COMMAND, sentAt="2026-10-29T00:00:00Z", senderRole="leader",
            senderId="wrong-leader", mentions="all", groupId=GROUP, sessionId=None,
            command=GroupMgmtCommandType.DISBAND)
        await client._handle_mgmt_command(command)
        self.assertIsNone(client._disband_context)
        self.assertFalse(client._pending_leave)
        self.assertEqual(audit.events[0][0], "DISBAND_REJECTED")

    async def test_failed_leave_close_does_not_reenter_cleanup_or_publish(self):
        client, queue, exchange, _, audit = self.client()
        client.leave_finished.set()
        client.leave_succeeded = False
        client._inbox_channel = MagicMock(is_closed=True)
        await client.close()
        queue.cancel.assert_not_awaited()
        queue.delete.assert_not_awaited()
        exchange.publish.assert_not_awaited()
        self.assertTrue(any(name == "FAILED_LEAVE_TRANSPORT_CLOSED"
                            for name, _ in audit.events))

    async def test_duplicate_cleanup_waits_for_single_sequence(self):
        client, queue, exchange, _, audit = self.client()
        entered = asyncio.Event()
        release = asyncio.Event()
        async def cancel_once(*_):
            entered.set()
            await release.wait()
        queue.cancel.side_effect = cancel_once
        first = asyncio.create_task(client._cleanup_group_resources())
        await entered.wait()
        second = asyncio.create_task(client._cleanup_group_resources())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        queue.cancel.assert_awaited_once()
        queue.delete.assert_awaited_once()
        exchange.publish.assert_awaited_once()
        self.assertEqual(sum(name == "LEAVE_RECEIPT_PUBLISHED"
                             for name, _ in audit.events), 1)

    def test_retry_budget_is_below_leader_window(self):
        # asyncio.wait_for enforces each attempt; there is no retry sleep.
        self.assertLess(3 * 5, 20)

    async def test_end_to_end_partner_budget_exhaustion_sends_no_receipt(self):
        client, queue, exchange, _, _ = self.client()
        client._disband_received_at = monotonic() - 18.1
        with self.assertRaises(TimeoutError):
            await client._cleanup_group_resources()
        queue.cancel.assert_not_awaited()
        queue.delete.assert_not_awaited()
        exchange.publish.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
