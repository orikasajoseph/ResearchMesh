"""Reader formal Group entry. Default only checks local configuration."""
import argparse
import asyncio
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv
from aip.amp_kafka_heartbeat import KafkaBackedHeartbeatEmitter
from aip.group_literature_partner import tls_context
from aip.group_reader_partner import serve
from aip.group_task_store import DurableTaskStore
from aip.literature_audit import AuditJournal
from aip.reader_unified_executor import ReaderExecutor

ROOT = Path(__file__).resolve().parent
CREDENTIAL_DIR = Path(r"D:\research\aic\credentials\reader")
TASK_DB_PATH = Path(r"D:\research\aic\state\group_tasks.sqlite3")


async def run_live(*, partner_aic, leader_aic, executor):
    audit = AuditJournal()
    print("AUDIT_JOURNAL:", audit.path, flush=True)
    store = DurableTaskStore(TASK_DB_PATH)
    heartbeat_path = ROOT / "logs" / "amp_heartbeat_reader_live.jsonl"
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat = KafkaBackedHeartbeatEmitter(
        heartbeat_path, aic=partner_aic, credential_dir=CREDENTIAL_DIR,
    )
    task = asyncio.create_task(heartbeat.run_periodic(float(os.getenv("AMP_HEARTBEAT_INTERVAL_SECONDS", "15"))))
    try:
        await serve(partner_aic=partner_aic, leader_aic=leader_aic, credential_dir=CREDENTIAL_DIR,
            task_store=store, reader_python=executor.python, reader_root=executor.reader_root,
            env_file=executor.env_file, runtime_home=executor.runtime_home, audit=audit)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Allow formal MQ and paid Reader execution after a Leader Start")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    partner = os.getenv("RM_READER_AIC", "").strip()
    leader = os.getenv("RM_ALLOWED_LEADER_AIC", "").strip()
    python = os.getenv("RM_READER_PYTHON", "").strip()
    if not partner or not leader or not python:
        print("MISSING_CONFIG: RM_READER_AIC, RM_ALLOWED_LEADER_AIC, RM_READER_PYTHON required")
        return 2
    executor = ReaderExecutor(reader_python=python,
        reader_root=os.getenv("RM_READER_WRAPPER_ROOT", str(ROOT / "agents" / "paper_reader")),
        env_file=args.env_file, runtime_home=os.getenv("RM_READER_RUNTIME_HOME") or None)
    try:
        tls_context(CREDENTIAL_DIR)
        executor.validate_runtime_paths()
    except (OSError, ValueError):
        print("READER_LOCAL_CONFIG_INVALID: inspect paths, credentials and cache")
        return 2
    if not args.serve:
        print("OFFLINE_CONFIG_OK: paths and mTLS only; no MQ, worker, PDF or model call")
        return 0
    from scripts.check_analysis_group_readiness import runtime_probe
    probe = runtime_probe(executor.python, "reader", args.env_file,
                          executor.runtime_home or executor.python.parent.parent / "runtime-cache")
    if not probe["passed"]:
        print("READER_RUNTIME_PREFLIGHT_FAILED:", probe["code"])
        return 2
    try:
        asyncio.run(run_live(partner_aic=partner, leader_aic=leader, executor=executor))
    except KeyboardInterrupt:
        print("STOPPED_BY_USER")
    except Exception:
        print("READER_PARTNER_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
