"""Explicit offline / temporary ACL check / invitation-only entry point."""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from group_invitation import AUTH_SERVICE_URL, InviteConfig, run_invitation
from platform_mq_check import ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leader-aic", required=True)
    parser.add_argument("--partner-aic", required=True)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--auth-service-url", default=AUTH_SERVICE_URL)
    parser.add_argument("--mode", choices=("offline", "acl-check", "invite"), default="offline")
    parser.add_argument("--execute", action="store_true", help="Explicitly permit live platform mutations")
    parser.add_argument("--partner-ready", action="store_true", help="Confirm Partner is currently listening")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--join-timeout", type=float, default=45)
    parser.add_argument("--hold-seconds", type=float, default=30)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "group-invitation")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)  # Never dump transport payloads or invitation tokens.
    directory = args.output_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "record.json"
    last_event_count = 0

    def checkpoint(record):
        nonlocal last_event_count
        # Save the generated group ID before every write/publish attempt. A forced
        # process kill cannot guarantee rollback; this record enables later inspection.
        temporary = directory / "record.pending.json"
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        for event in record["events"][last_event_count:]:
            print(event["event"], "Group:", record["groupId"], flush=True)
        last_event_count = len(record["events"])

    print("仅预检/ACL/邀请；禁止 TaskCommand 和科研 API。记录：", path, flush=True)
    config = InviteConfig(args.leader_aic, args.partner_aic, args.credentials_dir,
                          args.auth_service_url, args.timeout, args.join_timeout, args.hold_seconds)
    try:
        result = asyncio.run(run_invitation(config, mode=args.mode, execute=args.execute,
                                            partner_ready=args.partner_ready, checkpoint=checkpoint))
    except KeyboardInterrupt:
        print("已中断；请检查记录中的 cleanup 状态，不要直接重复发送邀请。")
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("本记录不代表 AIP 科研任务完成；TaskCommand 和 LLM 调用均为 0。")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
