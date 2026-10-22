"""Scan reachable Git blobs for credential patterns without printing secret text."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from io import BytesIO
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

PATTERNS = {
    "private_key_pem": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "bearer_token": re.compile(r"(?i)\bauthorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".txt", ".ps1", ".sh", ".bat", ".env", ".example", ".csv", ".xml",
})


def scan_text(text: str) -> list[dict]:
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for code, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append({"pattern": code, "line": line_number})
    return findings


def _git(repo: Path, *args: str, text=True):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=text, encoding="utf-8" if text else None,
                          errors="replace" if text else None).stdout


def audit_history(repo: Path, *, max_blob_bytes: int = 2_000_000) -> dict:
    repo = repo.resolve()
    if _git(repo, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise ValueError("NOT_A_GIT_WORKTREE")
    objects = {}
    for line in _git(repo, "rev-list", "--objects", "--all").splitlines():
        oid, _, path = line.partition(" ")
        if path:
            objects.setdefault(oid, path)
    selected = [(oid, path_value) for oid, path_value in objects.items()
                if Path(path_value).suffix.lower() in TEXT_SUFFIXES
                or Path(path_value).name in {"Dockerfile", "Makefile"}]
    findings, scanned, skipped = [], 0, 0
    process = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=repo, check=True,
        input="".join(oid + "\n" for oid, _ in selected).encode("ascii"),
        capture_output=True)
    output = BytesIO(process.stdout)
    for expected_oid, path_value in selected:
        header = output.readline().decode("ascii", errors="replace").strip().split()
        if len(header) != 3 or header[0] != expected_oid:
            raise ValueError("INVALID_GIT_BATCH_RESPONSE")
        _, kind, size_value = header
        size = int(size_value)
        raw = output.read(size)
        if output.read(1) != b"\n":
            raise ValueError("INVALID_GIT_BATCH_SEPARATOR")
        if kind != "blob" or size > max_blob_bytes or b"\x00" in raw:
            skipped += 1
            continue
        scanned += 1
        for finding in scan_text(raw.decode("utf-8", errors="replace")):
            findings.append({"objectId": expected_oid, "path": path_value, **finding})
    refs = _git(repo, "for-each-ref", "--format=%(refname)").splitlines()
    return {
        "schemaVersion": "researchmesh.git-history-audit.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "all_reachable_git_blobs", "networkAttempted": False,
        "refsInspected": len(refs), "textBlobsScanned": scanned,
        "largeOrBinaryBlobsSkipped": skipped, "findings": findings,
        "passed": not findings,
        "findingContentPersisted": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_history(args.repo)
    except Exception:
        report = {"passed": False, "error": "HISTORY_AUDIT_FAILED",
                  "networkAttempted": False, "findingContentPersisted": False}
    output = args.report or Path(r"D:\research\aic\evidence") / (
        "git-history-audit-" + uuid4().hex + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report["reportPath"] = str(output.resolve())
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({k: v for k, v in report.items() if k != "findings"},
                     ensure_ascii=False, indent=2))
    print("findingsCount:", len(report.get("findings", [])))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
