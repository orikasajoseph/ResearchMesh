"""Repository registered Group endpoint. Default invocation is offline-only.

--serve enables real AMQPS and a static CodeWiki subprocess for each NEW task.
Always configure the separate Python 3.12 pinned CodeWiki environment first.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv
from aip.amp_kafka_heartbeat import KafkaBackedHeartbeatEmitter
from aip.group_repository_partner import serve, tls_context
from aip.group_task_store import DurableTaskStore
from aip.literature_audit import AuditJournal
from aip.repository_unified_executor import RepositoryStaticExecutor

ROOT = Path(__file__).resolve().parent
CREDENTIAL_DIR = Path(r"D:\research\aic\credentials\repository")
TASK_DB_PATH = Path(r"D:\research\aic\state\group_tasks.sqlite3")
HEARTBEAT_PATH = ROOT / "logs" / "amp_heartbeat_repository_live.jsonl"


async def run_live(*, partner_aic: str, leader_aic: str,
                   repository_python: Path, wrapper_root: Path) -> None:
    # Fail closed before opening the platform's queue.
    audit = AuditJournal()
    print("AUDIT_JOURNAL:", audit.path, flush=True)
    task_store = DurableTaskStore(TASK_DB_PATH)
    print("GROUP_TASK_STORE_READY", flush=True)
    print("GROUP_TASK_DB_PATH:", TASK_DB_PATH, flush=True)
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    heartbeat = KafkaBackedHeartbeatEmitter(
        HEARTBEAT_PATH, aic=partner_aic, credential_dir=CREDENTIAL_DIR,
    )
    interval = float(os.getenv("AMP_HEARTBEAT_INTERVAL_SECONDS", "15"))
    task = asyncio.create_task(heartbeat.run_periodic(interval), name="repository-heartbeat")
    print("HEARTBEAT_STARTED", flush=True)
    print("HEARTBEAT_PATH:", HEARTBEAT_PATH, flush=True)
    try:
        await serve(
            partner_aic=partner_aic, leader_aic=leader_aic,
            credential_dir=CREDENTIAL_DIR, task_store=task_store,
            repository_python=repository_python, wrapper_root=wrapper_root, audit=audit,
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    partner = os.getenv("RM_REPOSITORY_AIC", "").strip()
    leader = os.getenv("RM_ALLOWED_LEADER_AIC", "").strip()
    python_value = os.getenv("RM_REPOSITORY_PYTHON", "").strip()
    wrapper_value = os.getenv(
        "RM_REPOSITORY_WRAPPER_ROOT", str(ROOT / "agents" / "repository"),
    ).strip()
    if not partner or not leader:
        print("MISSING_AIC: RM_REPOSITORY_AIC or RM_ALLOWED_LEADER_AIC")
        return 2
    try:
        tls_context(CREDENTIAL_DIR)
    except (OSError, ValueError) as exc:
        print("CERTIFICATE_CONFIG_INVALID:", type(exc).__name__)
        return 2
    if not python_value:
        print("REPOSITORY_PYTHON_NOT_CONFIGURED: set RM_REPOSITORY_PYTHON (Python 3.12 with pinned CodeWiki)")
        return 2
    executor = RepositoryStaticExecutor(
        repository_python=Path(python_value), wrapper_root=Path(wrapper_value),
    )
    try:
        executor.validate_runtime_paths()
    except ValueError as exc:
        print("REPOSITORY_RUNTIME_INVALID:", str(exc))
        return 2
    if not args.serve:
        print("OFFLINE_CONFIG_OK: no MQ, clone, CodeWiki or model call was made")
        return 0
    from scripts.check_analysis_group_readiness import runtime_probe
    if not runtime_probe(Path(python_value), "repository", args.env_file, ROOT)["passed"]:
        print("REPOSITORY_RUNTIME_PREFLIGHT_FAILED")
        return 2
    try:
        asyncio.run(run_live(
            partner_aic=partner, leader_aic=leader,
            repository_python=Path(python_value), wrapper_root=Path(wrapper_value),
        ))
    except KeyboardInterrupt:
        print("STOPPED_BY_USER")
    except Exception as exc:
        print("PARTNER_FAILED:", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
