"""Formal single-task Leader entry for Reader or Repository; offline by default."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from group_invitation import InviteConfig
from group_literature_leader import run_group_task, validate_platform_task
from platform_mq_check import CheckError, ROOT
from run_platform_literature import confirm_task_release


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-type", choices=("paper_read", "repository_analyze"), required=True)
    parser.add_argument("--leader-aic", required=True)
    parser.add_argument("--partner-aic", required=True)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--partner-ready", action="store_true")
    parser.add_argument("--partner-commit")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--join-timeout", type=float, default=45)
    parser.add_argument("--task-timeout", type=float)
    parser.add_argument("--confirm-after-join", action="store_true")
    parser.add_argument("--release-timeout", type=float, default=600)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    if args.execute and not args.confirm_after_join:
        print("正式分析任务要求 --confirm-after-join，避免 Partner 未就绪时发送。")
        return 2
    try:
        task = validate_platform_task(
            json.loads(args.task.read_text(encoding="utf-8-sig")), args.task_type)
    except (OSError, ValueError, TypeError, KeyError, CheckError):
        print("任务文件无效：需要匹配能力的严格七字段 Task。")
        return 2
    timeout = args.task_timeout or (690 if args.task_type == "paper_read" else 210)
    output = ROOT / "outputs" / f"platform-{args.task_type.replace('_', '-')}-" \
        f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    output.mkdir(parents=True, exist_ok=False)
    config = InviteConfig(args.leader_aic, args.partner_aic, args.credentials_dir,
                          timeout=args.timeout, join_timeout=args.join_timeout, hold_seconds=0)

    def checkpoint(record):
        pending = output / "record.pending.json"
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(output / "record.json")

    release = ((lambda record: confirm_task_release(record, args.release_timeout))
               if args.confirm_after_join else None)
    record = asyncio.run(run_group_task(
        config, task, task_type=args.task_type, execute=args.execute,
        partner_ready=args.partner_ready, partner_commit=args.partner_commit,
        task_timeout=timeout, checkpoint=checkpoint, task_release=release))
    print("OUTPUT:", output)
    print("passed:", record["passed"], "protocolCompleted:", record["protocolCompleted"],
          "businessReady:", record["businessReady"])
    if record["remoteStateUnknown"]:
        print("远端状态不确定：核对 Task 审计后再决定，禁止直接重发 Start。")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
