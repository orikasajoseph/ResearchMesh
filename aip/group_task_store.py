
"""Durable idempotency store for ResearchMesh Group tasks.

No MQ connection and no business execution.
A duplicate claim never authorizes automatic backend retry.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


STATES = frozenset({
    "reserved",
    "working",
    "awaiting_completion",
    "completed",
    "failed",
    "canceled",
    "uncertain",
})


class DurableTaskStore:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS task_claims (
                        agent_aic TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (agent_aic, session_id, task_id)
                    )
                """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _check_identity(
        agent_aic: str,
        session_id: str,
        task_id: str,
    ) -> None:
        if not all(
            isinstance(v, str) and v.strip()
            for v in (agent_aic, session_id, task_id)
        ):
            raise ValueError("TASK_IDENTITY_REQUIRED")

    def reserve(
        self,
        *,
        agent_aic: str,
        session_id: str,
        task_id: str,
        payload: dict,
    ) -> tuple[str, str]:
        """Atomically register a task before any backend execution.

        Returns:
            ("new", "reserved")       First occurrence.
            ("duplicate", state)      Same identity and payload.
            ("conflict", state)       Same identity, different payload.
        """
        self._check_identity(agent_aic, session_id, task_id)

        if not isinstance(payload, dict):
            raise ValueError("TASK_PAYLOAD_REQUIRED")

        canonical = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")

                row = db.execute(
                    """SELECT payload_sha256, state
                       FROM task_claims
                       WHERE agent_aic=? AND session_id=? AND task_id=?""",
                    (agent_aic, session_id, task_id),
                ).fetchone()

                if row is not None:
                    if row[0] != digest:
                        return "conflict", row[1]
                    return "duplicate", row[1]

                db.execute(
                    """INSERT INTO task_claims
                       (agent_aic, session_id, task_id, payload_sha256, state)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        agent_aic,
                        session_id,
                        task_id,
                        digest,
                        "reserved",
                    ),
                )
                return "new", "reserved"

    def advance_state(
        self,
        *,
        agent_aic: str,
        session_id: str,
        task_id: str,
        expected: tuple[str, ...],
        new_state: str,
    ) -> bool:
        """Compare-and-set state; never blindly overwrite cancellation."""
        self._check_identity(agent_aic, session_id, task_id)

        if (
            not expected
            or any(state not in STATES for state in expected)
            or new_state not in STATES
        ):
            raise ValueError("INVALID_TASK_STATE")

        placeholders = ",".join("?" for _ in expected)

        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    f"""UPDATE task_claims
                        SET state=?, updated_at=CURRENT_TIMESTAMP
                        WHERE agent_aic=? AND session_id=? AND task_id=?
                        AND state IN ({placeholders})""",
                    (
                        new_state,
                        agent_aic,
                        session_id,
                        task_id,
                        *expected,
                    ),
                )
                return cursor.rowcount == 1

    def lookup(
        self,
        *,
        agent_aic: str,
        session_id: str,
        task_id: str,
    ) -> str | None:
        self._check_identity(agent_aic, session_id, task_id)

        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT state FROM task_claims
                   WHERE agent_aic=? AND session_id=? AND task_id=?""",
                (agent_aic, session_id, task_id),
            ).fetchone()

        return row[0] if row else None
