
"""Run the registered Literature Group Partner on Windows.

Default:
    Offline configuration check only.
    No MQ connection or external research request.

--serve:
    Connect to official AMQPS.
    Start AMP heartbeat.
    Initialize the durable SQLite task store.
    Listen for trusted Group invitations.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv

from acps_sdk.amp import HeartbeatEmitter
from aip.group_literature_partner import (
    serve,
    tls_context,
)
from aip.group_task_store import DurableTaskStore


ROOT = Path(__file__).resolve().parent

# Formal Literature identity files.
CREDENTIAL_DIR = Path(
    r"D:\research\aic\credentials\literature"
)

# SQLite lives outside the Git repository.
TASK_DB_PATH = (
    CREDENTIAL_DIR.parents[1]
    / "state"
    / "group_tasks.sqlite3"
)

# AMP heartbeat output.
AMP_LOG_DIR = ROOT / "logs"

HEARTBEAT_PATH = (
    AMP_LOG_DIR
    / "amp_heartbeat_literature_live.jsonl"
)


async def run_live(
    *,
    partner_aic: str,
    leader_aic: str,
) -> None:
    """Start durable storage, heartbeat and formal Group Partner."""

    # Initialize SQLite before starting the MQ Partner.
    # Any database initialization error must stop startup.
    task_store = DurableTaskStore(TASK_DB_PATH)

    print("GROUP_TASK_STORE_READY", flush=True)
    print(
        "GROUP_TASK_DB_PATH:",
        TASK_DB_PATH,
        flush=True,
    )

    AMP_LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    heartbeat = HeartbeatEmitter(
        HEARTBEAT_PATH,
        aic=partner_aic,
    )

    interval = float(
        os.getenv(
            "AMP_HEARTBEAT_INTERVAL_SECONDS",
            "15",
        )
    )

    hb_task = asyncio.create_task(
        heartbeat.run_periodic(interval),
        name="literature-heartbeat",
    )

    print("HEARTBEAT_STARTED", flush=True)
    print(
        "HEARTBEAT_PATH:",
        HEARTBEAT_PATH,
        flush=True,
    )

    try:
        await serve(
            partner_aic=partner_aic,
            leader_aic=leader_aic,
            credential_dir=CREDENTIAL_DIR,
            task_store=task_store,
        )

    finally:
        hb_task.cancel()

        with contextlib.suppress(
            asyncio.CancelledError
        ):
            await hb_task


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--serve",
        action="store_true",
        help=(
            "Connect to official MQ and allow "
            "real Literature Group tasks."
        ),
    )

    args = parser.parse_args()

    load_dotenv(
        ROOT / ".env",
        override=False,
    )

    partner = os.getenv(
        "RM_LITERATURE_AIC",
        "",
    ).strip()

    leader = os.getenv(
        "RM_ALLOWED_LEADER_AIC",
        "",
    ).strip()

    if not partner or not leader:
        print(
            "MISSING_AIC: set RM_LITERATURE_AIC "
            "and RM_ALLOWED_LEADER_AIC"
        )
        return 2

    # Validate the client certificate configuration.
    try:
        tls_context(CREDENTIAL_DIR)

    except (OSError, ValueError) as exc:
        print(
            "CERTIFICATE_CONFIG_INVALID:",
            type(exc).__name__,
        )
        return 2

    # Default invocation is offline-only.
    if not args.serve:
        print(
            "OFFLINE_CONFIG_OK; "
            "no MQ connection or real search was made"
        )
        return 0

    # Do not start a real research Partner without the API key.
    if not os.getenv(
        "SEMANTIC_SCHOLAR_API_KEY"
    ):
        print(
            "SEMANTIC_SCHOLAR_API_KEY missing; "
            "refusing to start a research Partner"
        )
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
        print(
            "PARTNER_FAILED:",
            type(exc).__name__,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
