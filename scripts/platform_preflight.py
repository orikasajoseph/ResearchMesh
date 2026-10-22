"""Offline ResearchMesh platform preflight (three registered partner identities).

No platform requests, AMQP connections, model calls or provider API requests.
No secret contents are printed. No existing task database is modified.

Use: .venv\\Scripts\\python.exe -X utf8 scripts\\platform_preflight.py --all
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = Path(r"D:\research\aic\credentials") if os.name == "nt" else ROOT / "credentials"
STATE_DIR = CREDENTIALS.parent / "state"

AICS = {
    "literature": "1.2.156.3088.1.BUPT.RECKYJ.4K8V3F.0113BD.00TL",
    "reader": "1.2.156.3088.1.BUPT.RECKYJ.TFPYUF.2MAZ97.04LN",
    "repository": "1.2.156.3088.1.BUPT.RECKYJ.13NQBJ.YM4WBJ.0E0X",
}
LEADER_AIC = "1.2.156.3088.1.BUPT.RECKYJ.Q77J9R.52YOLZ.11P6"
EXPECTED_BRANCH_PREFIX = "wu/"


@dataclass(frozen=True)
class Check:
    section: str
    label: str
    state: str  # PASS, WARN, FAIL, INFO
    detail: str


class Reporter:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, section: str, label: str, state: str, detail: str) -> None:
        assert state in {"PASS", "WARN", "FAIL", "INFO"}
        self.checks.append(Check(section, label, state, detail))
        print(f"[{state:4}] {section:11} {label:29} {detail}")


def git(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True,
            encoding="utf-8", errors="replace", check=False, timeout=7,
        )
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def load_safe_env(path: Path) -> dict[str, str]:
    """Read dotenv keys without exporting them and without printing any values."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    return {str(k): str(v) for k, v in dotenv_values(path).items() if v is not None}


def check_git(report: Reporter) -> None:
    rc, head = git("rev-parse", "--short", "HEAD")
    report.add("git", "commit", "PASS" if rc == 0 else "FAIL", head if rc == 0 else "git unavailable")
    rc, branch = git("branch", "--show-current")
    report.add("git", "branch", "PASS" if rc == 0 and branch.startswith(EXPECTED_BRANCH_PREFIX) else "WARN", branch or "unresolved")
    rc, status = git("status", "--porcelain", "--untracked-files=normal")
    if rc:
        report.add("git", "working tree", "FAIL", "status unavailable")
    else:
        lines = status.splitlines() if status else []
        tracked = sum(not row.startswith("??") for row in lines)
        untracked = len(lines) - tracked
        state = "PASS" if tracked == 0 else "WARN"
        report.add("git", "working tree", state, f"tracked_changes={tracked}, untracked={untracked}")
    rc, tracked_secrets = git("ls-files", "--", ".env", "*.pem", "**/*.pem", "**/eab.json")
    secret_count = len(tracked_secrets.splitlines()) if tracked_secrets else 0
    report.add("security", "secret files tracked", "PASS" if rc == 0 and secret_count == 0 else "FAIL", f"count={secret_count}" if rc == 0 else "git unavailable")


def check_sdk(report: Reporter) -> None:
    spec = importlib.util.find_spec("acps_sdk")
    report.add("python", "ACPs SDK importable", "PASS" if spec is not None else "FAIL", "module located" if spec is not None else "module missing")
    try:
        v = importlib.metadata.version("acps-sdk")
    except importlib.metadata.PackageNotFoundError:
        v = None
    report.add("python", "ACPs SDK distribution", "PASS" if v == "2.2.0" else "WARN", f"version={v}" if v else "version not detectable")
    installed = importlib.util.find_spec("dotenv") is not None
    report.add("python", "python-dotenv", "PASS" if installed else "FAIL", "module located" if installed else "missing")


def _decode_cert(cert: Path) -> dict:
    # CPython exposes certificate fields without cryptography or a network call.
    # Do not inspect, print, or transmit the private key.
    return ssl._ssl._test_decode_cert(str(cert))


def cert_identity(cert: Path) -> tuple[str | None, int, int]:
    fields = _decode_cert(cert)
    cn = next((v for rdns in fields.get("subject", ()) for k, v in rdns if k == "commonName"), None)
    not_before = ssl.cert_time_to_seconds(fields["notBefore"])
    not_after = ssl.cert_time_to_seconds(fields["notAfter"])
    return cn, not_before, not_after


def check_identity(report: Reporter, agent: str, aic: str) -> None:
    d = CREDENTIALS / agent
    expected = ("agent-cert.pem", "agent-key.pem", "trust-bundle.pem")
    missing = [n for n in expected if not (d / n).is_file()]
    if missing:
        report.add(agent, "identity files", "FAIL", "missing=" + ",".join(missing))
        return
    report.add(agent, "identity files", "PASS", "certificate, key, trust bundle present")
    try:
        actual, start, end = cert_identity(d / "agent-cert.pem")
        now = time.time()
        valid = start <= now < end
        report.add(agent, "certificate identity", "PASS" if actual == aic else "FAIL", "CN matches approved AIC" if actual == aic else "CN mismatch")
        report.add(agent, "certificate validity", "PASS" if valid else "FAIL", f"days_remaining={max(0, int((end-now)/86400))}" if valid else "expired or not yet valid")
    except Exception as exc:
        report.add(agent, "certificate parse", "FAIL", type(exc).__name__)
    try:
        bundle = (d / "trust-bundle.pem").read_text(encoding="ascii")
        count = bundle.count("-----BEGIN CERTIFICATE-----")
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.load_verify_locations(cafile=str(d / "trust-bundle.pem"))
        context.load_cert_chain(certfile=str(d / "agent-cert.pem"), keyfile=str(d / "agent-key.pem"))
        report.add(agent, "mTLS local context", "PASS" if count else "FAIL", f"trust_certificates={count}, cert/key load ok" if count else "empty trust bundle")
    except Exception as exc:
        report.add(agent, "mTLS local context", "FAIL", type(exc).__name__)


def check_config(report: Reporter) -> None:
    path = ROOT / ".env"
    if not path.is_file():
        report.add("config", "Literature .env", "FAIL", "file missing")
        return
    env = load_safe_env(path)
    actual_lit = os.getenv("RM_LITERATURE_AIC") or env.get("RM_LITERATURE_AIC", "")
    actual_leader = os.getenv("RM_ALLOWED_LEADER_AIC") or env.get("RM_ALLOWED_LEADER_AIC", "")
    report.add("config", "Literature AIC binding", "PASS" if actual_lit == AICS["literature"] else "FAIL", "matches approved registration" if actual_lit == AICS["literature"] else "missing/mismatched")
    report.add("config", "Leader allowlist", "PASS" if actual_leader == LEADER_AIC else "FAIL", "matches formal Leader" if actual_leader == LEADER_AIC else "missing/mismatched")
    api_exists = bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY") or env.get("SEMANTIC_SCHOLAR_API_KEY"))
    report.add("config", "Literature provider key", "PASS" if api_exists else "WARN", "present (not displayed or validated)" if api_exists else "missing; real Literature --serve refuses startup")
    for agent in ("reader", "repository"):
        report.add("config", f"{agent} Group allowlist", "INFO", "production runtime configuration not yet verified")


def check_code(report: Reporter) -> None:
    targets = (
        "run_group_literature.py",
        "aip/group_literature_partner.py",
        "aip/group_literature_handler.py",
        "aip/group_task_store.py",
        "aip/literature_unified_executor.py",
        "business_execution.py",
        "reader_adapter.py",
        "repository_adapter.py",
    )
    failed = []
    for path in targets:
        src = ROOT / path
        try:
            compile(src.read_bytes(), str(src), "exec")
        except Exception:
            failed.append(path)
    report.add("code", "Literature business stack", "PASS" if not failed else "FAIL", "8 modules compile" if not failed else f"missing/invalid={','.join(failed)}")
    for agent, entry in (
        ("reader", ROOT / "run_group_reader.py"),
        ("repository", ROOT / "run_group_repository.py"),
    ):
        report.add(agent, "formal Group entry", "PASS" if entry.is_file() else "WARN", "file exists (not execution-tested)" if entry.is_file() else "not implemented at expected path")


def check_sqlite(report: Reporter) -> None:
    # Disposable file in production state directory; actual group_tasks.sqlite3 untouched.
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="preflight_", dir=str(STATE_DIR)) as d:
            conn = sqlite3.connect(str(Path(d) / "probe.sqlite3"))
            try:
                conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO probe (id) VALUES (1)")
                assert conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 1
                conn.commit()
            finally:
                conn.close()
        report.add("sqlite", "temporary WAL-safe probe", "PASS", "independent temporary DB; real task store untouched")
    except Exception as exc:
        report.add("sqlite", "temporary WAL-safe probe", "FAIL", type(exc).__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Check all three registered partner identities")
    parser.add_argument("--literature-only", action="store_true", help="Check Literature only")
    args = parser.parse_args()
    report = Reporter()
    print("ResearchMesh Offline Platform Preflight")
    print("Mode: OFFLINE; never connects to platform MQ, ADP, AMP, or paid providers")
    check_git(report)
    check_sdk(report)
    check_config(report)
    check_code(report)
    check_sqlite(report)
    targets = AICS if args.all or not args.literature_only else {"literature": AICS["literature"]}
    for agent, aic in targets.items():
        check_identity(report, agent, aic)
    report.add("platform", "live ADP/Group/AMP", "INFO", "not checked; require separate online evidence")
    counts = {s: sum(c.state == s for c in report.checks) for s in ("PASS", "WARN", "FAIL", "INFO")}
    print("\nSummary: " + ", ".join(f"{s}={counts[s]}" for s in counts))
    print("OFFLINE_ONLY: True")
    print("MQ_CONNECTIONS: 0")
    print("HTTP_REQUESTS: 0")
    print("PAID_API_CALLS: 0")
    print("OFFLINE_PREFLIGHT:", "FAIL" if counts["FAIL"] else "PARTIAL" if counts["WARN"] else "PASS")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
