"""Reader/Repository acceptance; backend count never claims model HTTP count."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from aip.literature_audit import AUDIT_ROOT
from verify_literature_acceptance import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("journal", "database", "group", "session", "task", "agent"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--task-type", required=True, choices=("paper_read", "repository_analyze"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.journal, args.database, group=args.group, session=args.session,
            task=args.task, agent=args.agent, require_http=False, expected_task_type=args.task_type)
    except Exception:
        report = dict(accepted=False, error="EVIDENCE_UNREADABLE_OR_INVALID")
    report.update(taskType=args.task_type, providerHttpCountVerified=False,
                  note="One backend invocation is not one model HTTP request; Reader may issue multiple model calls.")
    output = args.report or AUDIT_ROOT / ("analysis-acceptance-" + uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report["reportPath"] = str(output.resolve())
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
