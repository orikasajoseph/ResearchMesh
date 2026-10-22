"""Read-only acceptance verifier. Never imports/starts MQ or provider clients."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from aip.literature_audit import AUDIT_ROOT, HISTORICAL_TASK, digest, read_events
from result_contract import validate_result


def verify(journal, database, *, group, session, task, agent, require_formal=True):
    events = read_events(journal)  # Malformed/truncated journals must fail, never skip lines.
    if not events or [e["sequence"] for e in events] != list(range(1, len(events) + 1)):
        raise ValueError("INVALID_JOURNAL_SEQUENCE")
    if len({e["runId"] for e in events}) != 1 or len({e["mode"] for e in events}) != 1:
        raise ValueError("MIXED_JOURNAL")
    formal = not require_formal or events[0]["mode"] == "formal"
    grouped = [e for e in events if e.get("groupId") == group]
    selected = [e for e in grouped if e.get("sessionId") == session and e.get("taskId") == task]
    def named(name, source=selected):
        return [e for e in source if e["event"] == name]
    # URI read-only: no DurableTaskStore initialization, DDL, WAL mode changes or claims.
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT state FROM task_claims WHERE agent_aic=? AND session_id=? AND task_id=?",
                         (agent, session, task)).fetchone()
    completed = named("SQLITE_COMPLETED")
    sqlite_ok = bool(row and row[0] == "completed" and len(completed) == 1
                     and completed[0]["sqliteState"] == "completed")
    starts, ends = named("HTTP_STARTED"), named("HTTP_FINISHED")
    http_ok = (len(starts) == len(ends) == 1 and starts[0]["requestId"] == ends[0]["requestId"]
               and starts[0]["requestSequence"] == ends[0]["requestSequence"] == 1
               and starts[0]["sequence"] < ends[0]["sequence"]
               and ends[0]["httpStatus"] == 200 and ends[0]["exceptionCategory"] is None)
    results, readbacks = named("RESULT_SAVED"), named("RESULT_READBACK_VALID")
    result_ok = False
    if len(results) == len(readbacks) == 1:
        saved = results[0]
        result = saved["result"]
        validate_result(result, result)
        result_ok = (len(result) == 6 and digest(result) == saved["redactedSha256"]
                     and saved["source"] == "partner_redacted_copy" and saved["originalSchemaValid"] is True
                     and len(saved["originalSha256"]) == 64
                     and readbacks[0]["resultSequence"] == saved["sequence"]
                     and saved["sequence"] < readbacks[0]["sequence"])
    lifecycle_names = ("DISBAND_RECEIVED", "LEAVE_ENTERED", "CLEANUP_ENTERED", "GROUP_LEFT",
                       "CLEANUP_SUCCEEDED", "LEAVE_FINISHED")
    lifecycle = [named(name, grouped) for name in lifecycle_names]
    lifecycle_ok = all(len(items) == 1 for items in lifecycle)
    if lifecycle_ok:
        order = [items[0]["sequence"] for items in lifecycle]
        lifecycle_ok = (order == sorted(order) and lifecycle[0][0]["pendingLeave"] is True
                        and lifecycle[3][0]["groupResourcesCleared"] is True
                        and lifecycle[4][0]["groupResourcesCleared"] is True
                        and not lifecycle[4][0]["failures"]
                        and bool(completed) and completed[0]["sequence"] < order[0])
    backend_start, backend_end = named("BACKEND_STARTED"), named("BACKEND_FINISHED")
    failures = [e for e in grouped if e["event"] in {
        "TASK_FAILED", "COMMAND_EXCEPTION", "CLEANUP_FAILED", "LEAVE_FAILED", "LEAVE_STATUS_FAILED", "SDK_MESSAGE_FAILED"}]
    reservations = named("TASK_RESERVATION")
    joined = named("GROUP_JOINED", grouped)
    single_task = {(e.get("sessionId"), e.get("taskId")) for e in grouped if e["event"] == "TASK_COMMAND"}
    clean = (len(backend_start) == len(backend_end) == 1 and backend_end[0]["outcome"] == "success"
             and backend_start[0]["sequence"] < backend_end[0]["sequence"]
             and not failures and len(joined) == 1
             and joined[0]["sequence"] < backend_start[0]["sequence"]
             and single_task == {(session, task)} and sum(e["decision"] == "new" for e in reservations) == 1
             and not any(e["decision"] == "conflict" for e in reservations)
             and all(e.get("agentAic", agent) == agent for e in grouped)
             and lifecycle_ok)
    if clean and http_ok and result_ok and sqlite_ok:
        clean = (backend_start[0]["sequence"] < starts[0]["sequence"] < ends[0]["sequence"]
                 < backend_end[0]["sequence"] < results[0]["sequence"]
                 < readbacks[0]["sequence"] < completed[0]["sequence"])
    checks = dict(sqlite_completed=sqlite_ok, exactly_one_actual_http=http_ok,
                  result_readback_schema_valid=result_ok, disband_cleanup_group_left=lifecycle_ok,
                  no_duplicate_execution_errors_or_resources=clean)
    eligible = formal and task != HISTORICAL_TASK
    return dict(accepted=eligible and all(checks.values()), eligible=eligible,
                checks=checks, groupId=group, sessionId=session, taskId=task,
                runId=events[0]["runId"], evidenceSource="partner_audit_not_leader_cache",
                httpRequests=len(starts), backendExecutions=len(backend_start))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "database", "group", "session", "task", "agent"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.journal, args.database, group=args.group, session=args.session,
                        task=args.task, agent=args.agent)
    except Exception:
        report = dict(accepted=False, error="EVIDENCE_UNREADABLE_OR_INVALID")
    output = args.report or AUDIT_ROOT / ("acceptance-" + uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report["reportPath"] = str(output.resolve())
    # Exclusive creation: an existing acceptance report is never replaced.
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
