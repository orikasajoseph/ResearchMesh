"""Run a real, allowlisted Literature Group Partner on Windows.

Default: OFFLINE local configuration check, no MQ/network/LLM.
--serve: connects to the official MQ, consumes Inbox, runs Semantic Scholar tasks.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from aip.group_literature_partner import serve, tls_context

ROOT = Path(__file__).resolve().parent
CREDENTIAL_DIR = Path(r"D:\research\aic\credentials\literature")


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
        asyncio.run(serve(partner_aic=partner, leader_aic=leader, credential_dir=CREDENTIAL_DIR))
    except KeyboardInterrupt:
        print("STOPPED_BY_USER")
    except Exception as exc:
        print("PARTNER_FAILED:", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
