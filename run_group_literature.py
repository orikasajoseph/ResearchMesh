"""Run a real, allowlisted Literature Group Partner on Windows.

Default: OFFLINE local configuration check, no MQ/network/LLM.
--serve: connects to the official MQ, consumes Inbox, runs Semantic Scholar tasks.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv
from aip.group_literature_partner import serve, tls_context
from acps_sdk.amp import HeartbeatEmitter

ROOT = Path(__file__).resolve().parent
CREDENTIAL_DIR = Path(r"D:\research\aic\credentials\literature")

AMP_LOG_DIR = ROOT / "logs"
HEARTBEAT_PATH = AMP_LOG_DIR / "amp_heartbeat_literature_live.jsonl"

async def run_live(*, partner_aic: str, leader_aic: str) -> None:
    AMP_LOG_DIR.mkdir(parents=True, exist_ok=True)

    heartbeat = HeartbeatEmitter(
        HEARTBEAT_PATH,
        aic=partner_aic,
    )

    interval = float(
        os.getenv("AMP_HEARTBEAT_INTERVAL_SECONDS", "15")
    )

    hb_task = asyncio.create_task(
        heartbeat.run_periodic(interval),
        name="literature-heartbeat",
    )

    print("HEARTBEAT_STARTED")
    print("HEARTBEAT_PATH:", HEARTBEAT_PATH)

    try:
        await serve(
            partner_aic=partner_aic,
            leader_aic=leader_aic,
            credential_dir=CREDENTIAL_DIR,
        )
    finally:
        hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb_task

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Real MQ and research: must opt in")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)

    partner = os.getenv("RM_LITERATURE_AIC", "").strip()
    leader = os.getenv("RM_ALLOWED_LEADER_AIC", "").strip()
    if not partner or not leader:
        print("MISSING_AIC: set RM_LITERATURE_AIC and RM_ALLOWED_LEADER_AIC")
        return 2
    try:
        tls_context(CREDENTIAL_DIR)
    except (OSError, ValueError) as exc:
        print("CERTIFICATE_CONFIG_INVALID:", type(exc).__name__)
        return 2

    if not args.serve:
        print("OFFLINE_CONFIG_OK; no MQ connection or real search was made")
        return 0
    if not os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
        print("SEMANTIC_SCHOLAR_API_KEY missing; refusing to start a research Partner")
        return 2
    try:
        asyncio.run(
            run_live(
            partner_aic=partner,
            leader_aic=leader,
            )
        )
    except KeyboardInterrupt:
        print("STOPPED_BY_USER")
    except Exception as exc:
        print("PARTNER_FAILED:", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
