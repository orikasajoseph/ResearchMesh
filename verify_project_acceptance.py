"""Aggregate three independent formal Group acceptances without network access."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from aip.literature_audit import AUDIT_ROOT
from verify_literature_acceptance import verify

TASK_TYPES = ("literature_search", "paper_read", "repository_analyze")
FIELDS = frozenset({"taskType", "journal", "database", "group", "session", "task", "agent"})


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_project(manifest: dict, *, verifier=verify) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != {"schemaVersion", "runs"}:
        raise ValueError("INVALID_PROJECT_MANIFEST")
    if manifest["schemaVersion"] != "researchmesh.formal-acceptance.v1":
        raise ValueError("UNSUPPORTED_PROJECT_MANIFEST")
    runs = manifest["runs"]
    if not isinstance(runs, list) or len(runs) != 3:
        raise ValueError("EXACTLY_THREE_AGENT_RUNS_REQUIRED")
    by_type = {}
    for item in runs:
        if not isinstance(item, dict) or set(item) != FIELDS or item["taskType"] in by_type:
            raise ValueError("INVALID_OR_DUPLICATE_AGENT_RUN")
        if item["taskType"] not in TASK_TYPES or not all(
            isinstance(item[name], str) and item[name].strip() for name in FIELDS
        ):
            raise ValueError("INVALID_AGENT_RUN_VALUE")
        by_type[item["taskType"]] = item
    if tuple(sorted(by_type, key=TASK_TYPES.index)) != TASK_TYPES:
        raise ValueError("MISSING_AGENT_RUN")

    identifiers = {name: [by_type[t][name] for t in TASK_TYPES]
                   for name in ("group", "session", "task", "agent")}
    uniqueness = {name: len(set(values)) == 3 for name, values in identifiers.items()}
    reports, evidence = {}, {}
    for task_type in TASK_TYPES:
        item = by_type[task_type]
        journal = Path(item["journal"])
        report = verifier(
            journal, item["database"], group=item["group"], session=item["session"],
            task=item["task"], agent=item["agent"], require_formal=True,
            require_http=task_type == "literature_search", expected_task_type=task_type)
        reports[task_type] = report
        evidence[task_type] = {
            "journalSha256": _sha256(journal), "runId": report.get("runId"),
            "groupId": item["group"], "sessionId": item["session"],
            "taskId": item["task"], "agentAic": item["agent"],
        }
    checks = {
        "three_task_types_present": set(by_type) == set(TASK_TYPES),
        "fresh_distinct_groups": uniqueness["group"],
        "fresh_distinct_sessions": uniqueness["session"],
        "fresh_distinct_tasks": uniqueness["task"],
        "distinct_registered_agents": uniqueness["agent"],
        "all_agent_acceptances_passed": all(r.get("accepted") is True for r in reports.values()),
    }
    return {
        "schemaVersion": "researchmesh.formal-acceptance-report.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "accepted": all(checks.values()), "checks": checks,
        "agentReports": reports, "evidence": evidence,
        "scope": "three_separate_formal_group_runs",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        report = verify_project(manifest)
    except Exception:
        report = {"accepted": False, "error": "PROJECT_EVIDENCE_UNREADABLE_OR_INVALID"}
    output = args.report or AUDIT_ROOT / ("project-acceptance-" + uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report["reportPath"] = str(output.resolve())
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
