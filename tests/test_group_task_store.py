
"""Offline tests for durable Group task idempotency."""

import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from aip.group_task_store import DurableTaskStore


class DurableTaskStoreTests(unittest.TestCase):

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.store = DurableTaskStore(self.path)

        self.identity = {
            "agent_aic": "test-literature-aic",
            "session_id": "session-1",
            "task_id": "task-1",
        }

        self.payload = {
            "schemaVersion": "0.2",
            "subtaskId": "S1",
            "taskType": "literature_search",
            "goal": "Find LoRA paper",
            "input": {"query": "LoRA"},
            "constraints": {"maxResults": 1},
            "outputSchema": "paper_records.v1",
        }

    def claim(self, **changes):
        identity = dict(self.identity)
        identity.update(changes)
        return self.store.reserve(
            **identity, payload=self.payload
        )

    def test_first_and_duplicate(self):
        self.assertEqual(
            self.claim(), ("new", "reserved")
        )
        self.assertEqual(
            self.claim(), ("duplicate", "reserved")
        )

    def test_payload_conflict(self):
        self.claim()
        different = dict(self.payload)
        different["goal"] = "Different research"

        self.assertEqual(
            self.store.reserve(
                **self.identity, payload=different
            ),
            ("conflict", "reserved"),
        )

    def test_restart_preserves_claim(self):
        self.claim()
        restarted = DurableTaskStore(self.path)

        self.assertEqual(
            restarted.reserve(
                **self.identity, payload=self.payload
            ),
            ("duplicate", "reserved"),
        )

    def test_agent_identity_isolated(self):
        self.assertEqual(self.claim()[0], "new")
        self.assertEqual(
            self.claim(agent_aic="other-agent")[0],
            "new",
        )

    def test_parallel_claim_only_one_new(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: self.claim(), range(8)
            ))

        self.assertEqual(
            sum(r[0] == "new" for r in results), 1
        )
        self.assertEqual(
            sum(r[0] == "duplicate" for r in results), 7
        )

    def test_state_transition_and_cancel_guard(self):
        self.claim()

        self.assertTrue(self.store.advance_state(
            **self.identity,
            expected=("reserved",),
            new_state="working",
        ))

        self.assertTrue(self.store.advance_state(
            **self.identity,
            expected=("working",),
            new_state="canceled",
        ))

        self.assertFalse(self.store.advance_state(
            **self.identity,
            expected=("working",),
            new_state="awaiting_completion",
        ))

        self.assertEqual(
            self.store.lookup(**self.identity),
            "canceled",
        )

    def test_state_survives_restart(self):
        self.claim()

        self.store.advance_state(
            **self.identity,
            expected=("reserved",),
            new_state="uncertain",
        )

        restarted = DurableTaskStore(self.path)

        self.assertEqual(
            restarted.lookup(**self.identity),
            "uncertain",
        )

        self.assertEqual(
            restarted.reserve(
                **self.identity, payload=self.payload
            ),
            ("duplicate", "uncertain"),
        )

    def test_invalid_database_fails(self):
        directory = Path(self.temp.name) / "not_a_db"
        directory.mkdir()

        with self.assertRaises((sqlite3.Error, OSError)):
            DurableTaskStore(directory)


if __name__ == "__main__":
    unittest.main()
