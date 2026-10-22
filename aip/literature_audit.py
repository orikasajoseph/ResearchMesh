"""Append-only acceptance evidence. No credentials, payloads or provider text.

Each process owns an exclusive journal; fsync precedes return. A torn or
unwritable journal fails closed, never truncates/repairs previous evidence.
ContextVars propagate through asyncio.wait_for to the actual HTTP send seam.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from result_contract import validate_result

AUDIT_ROOT = Path(r"D:\research\aic\evidence")
HISTORICAL_TASK = "task-e88431cbb7054355a8c19e3078219832"
CURRENT = ContextVar("literature_audit", default=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_events(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class AuditJournal:
    def __init__(self, root=AUDIT_ROOT, *, mode="formal"):
        if mode not in {"formal", "offline"}:
            raise ValueError("INVALID_AUDIT_MODE")
        self.run_id = uuid4().hex
        self.mode = mode
        self._lock = threading.RLock()
        self._sequence = 0
        self._requests = {}
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ("literature-" + self.run_id + ".jsonl")
        # Exclusive creation prevents overwriting existing evidence, even across processes.
        with self.path.open("x", encoding="utf-8"):
            pass
        self.emit("AUDIT_STARTED")

    def emit(self, event, **fields):
        with self._lock:
            self._sequence += 1
            record = dict(fields, event=event, runId=self.run_id, mode=self.mode,
                          sequence=self._sequence, at=datetime.now(timezone.utc).isoformat())
            data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode()
            with self.path.open("ab", buffering=0) as stream:
                if stream.write(data) != len(data):
                    raise OSError("AUDIT_SHORT_WRITE")
                os.fsync(stream.fileno())
            return record

    def request_started(self, identity, endpoint):
        with self._lock:
            key = tuple(identity[k] for k in ("groupId", "sessionId", "taskId"))
            number = self._requests.get(key, 0) + 1
            self._requests[key] = number
            request_id = uuid4().hex
            self.emit("HTTP_STARTED", **identity, requestId=request_id,
                      requestSequence=number, endpoint=endpoint)
            return request_id, number

    def save_result(self, identity, task, result):
        validate_result(result, task)
        if len(result) != 6:
            raise ValueError("EXACTLY_SIX_RESULT_FIELDS_REQUIRED")
        # Retain only schema shape and hashed paper identity; discard all free text,
        # URLs (which may contain tokens), extra data, warnings and provider bodies.
        redacted = {"schemaVersion": result["schemaVersion"], "subtaskId": result["subtaskId"],
                    "taskType": result["taskType"], "summary": "[redacted]",
                    "items": [{"kind": "paper", "data": {
                        "paperId": digest(item["data"]), "title": "[redacted]",
                        "url": "https://evidence.invalid/redacted/" + digest(item["data"])}}
                              for item in result["items"]],
                    "warnings": ["[redacted]" for _ in result["warnings"]]}
        validate_result(redacted, task)
        record = self.emit("RESULT_SAVED", **identity, result=redacted,
                           originalSha256=digest(result), redactedSha256=digest(redacted),
                           originalSchemaValid=True, source="partner_redacted_copy")
        loaded = next(e for e in read_events(self.path) if e["sequence"] == record["sequence"])
        if loaded != record or digest(loaded["result"]) != loaded["redactedSha256"]:
            raise ValueError("RESULT_READBACK_MISMATCH")
        validate_result(loaded["result"], task)
        self.emit("RESULT_READBACK_VALID", **identity, resultSequence=record["sequence"])


@contextmanager
def task_scope(journal, identity):
    token = CURRENT.set((journal, identity) if journal else None)
    try:
        yield
    finally:
        CURRENT.reset(token)


def task_event(event, **fields):
    context = CURRENT.get()
    if context:
        journal, identity = context
        return journal.emit(event, **identity, **fields)
