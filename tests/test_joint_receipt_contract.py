"""Cross-side contract with real candidate classes and mocked AMQP only."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from acps_sdk.aip import GroupMgmtResult
import group_literature_leader as leader
from tests import test_partner_leave_receipt as partner_fixture


PARTNER = partner_fixture.PARTNER
GROUP = partner_fixture.GROUP
SESSION = partner_fixture.SESSION
COMMAND = partner_fixture.COMMAND


class JointContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_partner_receipt_matches_s3_leader(self):
        partner, _, exchange, operations, _ = partner_fixture.PartnerLeaveReceiptTests().client()
        with patch("socket.getaddrinfo", side_effect=AssertionError("NO_NETWORK")):
            await partner._cleanup_group_resources()
        msg = GroupMgmtResult.model_validate(json.loads(exchange.publish.await_args.args[0].body))
        receiver = leader.RepositoryConversation(SimpleNamespace(partner_aic=PARTNER), GROUP, "task", SESSION)
        receiver.leave_command_id = COMMAND
        await receiver.on_message(msg)
        self.assertTrue(receiver.leave_completed.is_set())
        original = dict(receiver.leave_receipt)
        await receiver.on_message(msg)
        self.assertEqual(original, receiver.leave_receipt)
        self.assertEqual(operations[:3], ["cancel", "delete", "publish"])

    async def test_failed_partner_cleanup_cannot_confirm(self):
        partner, queue, exchange, _, _ = partner_fixture.PartnerLeaveReceiptTests().client()
        queue.delete.side_effect = RuntimeError("failed")
        with patch("socket.getaddrinfo", side_effect=AssertionError("NO_NETWORK")):
            with self.assertRaises(RuntimeError):
                await partner._cleanup_group_resources()
        exchange.publish.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
