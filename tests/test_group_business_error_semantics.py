"""Offline regression for fixed-code unified business errors and durable state.

This test is fake-client/fake-executor only: no MQ, HTTP, Model or paid requests.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from aip.group_literature_handler import LiteratureGroupHandler
from aip.group_task_store import DurableTaskStore
from business_execution import BusinessExecutionError
from test_group_durable_handler import FakeClient, LEADER, PARTNER, SESSION, TASK, command


class BusinessErrorSemanticsTests(unittest.IsolatedAsyncioTestCase):

    async def run_error(self, code, *, remote_execution_uncertain):
        with TemporaryDirectory() as d:
            path = Path(d) / "claims.sqlite3"
            client = FakeClient()
            backend = AsyncMock(side_effect=BusinessExecutionError(
                code,
                remote_execution_uncertain=remote_execution_uncertain,
            ))
            store = DurableTaskStore(path)
            handler = LiteratureGroupHandler(
                client, leader_aic=LEADER,
                executor=backend, task_store=store,
            )
            await handler.on_command(command(), True)
            actual = store.lookup(
                agent_aic=PARTNER, session_id=SESSION, task_id=TASK
            )
            sent = client.fail_task.await_args
            self.assertIsNotNone(sent)
            self.assertEqual(sent.args[:2], (TASK, SESSION))
            self.assertEqual(backend.await_count, 1)
            self.assertEqual(client.submit_for_completion.await_count, 0)

            # Restart and receive the same platform Start: never call upstream again.
            new_client = FakeClient()
            restarted = LiteratureGroupHandler(
                new_client, leader_aic=LEADER,
                executor=backend, task_store=DurableTaskStore(path),
            )
            await restarted.on_command(command(), True)
            self.assertEqual(backend.await_count, 1)
            new_client.accept_task.assert_not_awaited()
            return sent.args[2], actual

    async def test_invalid_task_is_deterministic_failed(self):
        a, state = await self.run_error(
            "INVALID_BUSINESS_TASK", remote_execution_uncertain=False,
        )
        self.assertEqual(a, "INVALID_BUSINESS_TASK")
        self.assertEqual(state, "failed")

    async def test_config_rejection_is_deterministic_failed(self):
        a, state = await self.run_error(
            "BUSINESS_EXECUTION_NOT_AUTHORIZED",
            remote_execution_uncertain=False,
        )
        self.assertEqual(a, "BUSINESS_EXECUTION_NOT_AUTHORIZED")
        self.assertEqual(state, "failed")

    async def test_timeout_is_uncertain(self):
        a, state = await self.run_error(
            "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED",
            remote_execution_uncertain=True,
        )
        self.assertEqual(a, "BACKEND_TIMEOUT_EXECUTION_MAY_HAVE_OCCURRED")
        self.assertEqual(state, "uncertain")

    async def test_bad_result_is_uncertain(self):
        a, state = await self.run_error(
            "INVALID_BUSINESS_RESULT", remote_execution_uncertain=True,
        )
        self.assertEqual(a, "INVALID_BUSINESS_RESULT")
        self.assertEqual(state, "uncertain")

    async def test_unknown_code_never_leaks_or_false_marks_failed(self):
        a, state = await self.run_error(
            "PROVIDER_SECRET_BEARING_MESSAGE", remote_execution_uncertain=False,
        )
        self.assertEqual(a, "LITERATURE_BACKEND_ERROR")
        self.assertEqual(state, "uncertain")


if __name__ == "__main__":
    unittest.main()
