
"""Offline tests for Literature Group lifecycle callbacks.

No external AMQP connections.
No actual invitation is sent.
No research backend is called.
"""

import asyncio
import unittest

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from acps_sdk.aip import GroupMgmtCommandType

from aip.group_literature_partner import serve


class FakeClient:
    group_id = "rm-test-group"

    def __init__(self, ready):
        self.ready = ready
        self.mgmt_handler = None
        self.disconnect_handler = None
        self.closed = False
        self.leave_finished = asyncio.Event()
        self.leave_succeeded = False

    def set_command_handler(self, handler):
        pass

    def set_invitation_handler(self, handler):
        pass

    def set_mgmt_command_handler(self, handler):
        self.mgmt_handler = handler

    def set_disconnect_handler(self, handler):
        self.disconnect_handler = handler

    async def connect(self):
        pass

    async def start_inbox_consuming(self, handler):
        self.ready.set()

    async def close(self):
        self.closed = True


class GroupLifecycleLoggingTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_disband_and_group_left(self):
        ready = asyncio.Event()
        client = FakeClient(ready)

        output = StringIO()

        # This test only checks callback registration and logs.
        # It deliberately does not initialize a real SQLite DB.
        fake_store = MagicMock()

        with (
            patch(
                "aip.group_literature_partner.GroupPartnerMqClient",
                return_value=client,
            ),
            patch(
                "aip.group_literature_partner.tls_context",
                return_value=object(),
            ),
            redirect_stdout(output),
        ):
            task = asyncio.create_task(
                serve(
                    partner_aic="partner-aic",
                    leader_aic="leader-aic",
                    credential_dir=Path("."),
                    task_store=fake_store,
                )
            )

            try:
                await asyncio.wait_for(
                    ready.wait(),
                    timeout=2,
                )

                self.assertIsNotNone(
                    client.mgmt_handler
                )

                self.assertIsNotNone(
                    client.disconnect_handler
                )

                valid_command = SimpleNamespace(
                    command=GroupMgmtCommandType.DISBAND,
                    groupId="rm-test-group",
                    senderRole="leader",
                    senderId="leader-aic",
                    must_respond=(
                        lambda aic: aic == "partner-aic"
                    ),
                )

                await client.mgmt_handler(
                    valid_command
                )

                invalid_command = SimpleNamespace(
                    command=GroupMgmtCommandType.DISBAND,
                    groupId="rm-test-group",
                    senderRole="leader",
                    senderId="untrusted-leader",
                    must_respond=lambda aic: True,
                )

                await client.mgmt_handler(
                    invalid_command
                )

                await client.disconnect_handler(
                    client,
                    "rm-test-group",
                )

            finally:
                task.cancel()

                with self.assertRaises(
                    asyncio.CancelledError
                ):
                    await task

        logs = output.getvalue()

        self.assertEqual(
            logs.count("DISBAND_RECEIVED"),
            1,
        )

        self.assertIn(
            "GROUP_LEFT",
            logs,
        )

        self.assertIn(
            "Group: rm-test-group",
            logs,
        )

        self.assertNotIn("INBOX_RESTART_REQUIRED", logs)

        self.assertTrue(
            client.closed
        )

        print("LIFECYCLE_CALLBACKS_OK: True")
        print("UNTRUSTED_LEADER_FILTERED: True")
        print("OFFLINE_ONLY: True")


if __name__ == "__main__":
    unittest.main()
