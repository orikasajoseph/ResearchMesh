"""Formal three-Agent chain: three fresh Groups with manual Start release."""
import argparse
import asyncio
import importlib.util
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import uuid4

from group_invitation import InviteConfig
from group_literature_leader import (
    run_group_literature, run_group_reader, run_group_repository_chain,
)
from platform_mq_check import ROOT
from platform_three_agent_orchestrator import (
    execute_platform_chain, validate_chain, read_json, write_json, stage_directory,
)
from platform_chain_resources import prepare_reader_resources, verify_cached_preparation
from run_platform_literature import confirm_task_release


STAGES = {
    "literature_search": (run_group_literature, "literature"),
    "paper_read": (run_group_reader, "reader"),
    "repository_analyze": (run_group_repository_chain, "repository"),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=ROOT / "examples/platform_three_agent_chain_v02.json")
    parser.add_argument("--leader-aic", required=True)
    parser.add_argument("--literature-aic", required=True)
    parser.add_argument("--reader-aic", required=True)
    parser.add_argument("--repository-aic", required=True)
    parser.add_argument("--literature-commit", required=True)
    parser.add_argument("--reader-commit", required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--partners-ready", action="store_true")
    parser.add_argument("--confirm-each-stage", action="store_true")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--resume-unstarted", action="store_true",
                        help="仅恢复有完整证据证明未发送Start的阶段；保留旧记录")
    parser.add_argument("--resume-terminal-failed", action="store_true",
                        help="人工恢复已明确failed且完整清理的阶段；远端状态未知时拒绝")
    parser.add_argument("--resume-business-completed", action="store_true",
                        help="仅恢复业务与协议已完成、但旧Group清理未确认的S1结果；保留限制")
    parser.add_argument("--business-cleanup-proof", type=Path,
                        help="独立通过的invitation→Disband清理协议record.json")
    parser.add_argument("--resource-proof", type=Path,
                        help="复用与本轮S1绑定的已核验资源记录；再次检查PDF缓存，不新增资源请求")
    parser.add_argument("--join-timeout", type=float, default=90)
    parser.add_argument("--release-timeout", type=float, default=1200)
    parser.add_argument("--literature-timeout", type=float, default=180)
    parser.add_argument("--reader-timeout", type=float, default=750)
    parser.add_argument("--repository-timeout", type=float, default=300)
    args = parser.parse_args(argv)
    plan_path = args.resume / "plan.json" if args.resume else args.plan
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    validate_chain(plan)
    if not args.execute:
        print("离线校验通过；未连接平台、未创建Group、未发送任务。")
        return 0
    if not args.partners_ready or not args.confirm_each_stage:
        print("正式连续联调必须同时指定 --partners-ready 和 --confirm-each-stage。")
        return 2
    if args.resume_business_completed and (not args.resume or not args.business_cleanup_proof):
        print("--resume-business-completed 必须同时指定 --resume 和 --business-cleanup-proof。未连接平台。")
        return 2
    if args.business_cleanup_proof and not args.resume_business_completed:
        print("--business-cleanup-proof 仅可与 --resume-business-completed 同时使用。未连接平台。")
        return 2
    if importlib.util.find_spec("pypdf") is None:
        print("缺少Leader PDF校验依赖；请安装 requirements-platform-resources.txt。未连接平台。")
        return 2
    commits = {"literature_search": args.literature_commit,
               "paper_read": args.reader_commit,
               "repository_analyze": args.repository_commit}
    aics = {"literature_search": args.literature_aic,
            "paper_read": args.reader_aic,
            "repository_analyze": args.repository_aic}
    timeouts = {"literature_search": args.literature_timeout,
                "paper_read": args.reader_timeout,
                "repository_analyze": args.repository_timeout}
    output = (args.resume.resolve() if args.resume else ROOT / "outputs/platform-three-agent" /
              f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}")

    async def runner(stage, task):
        function, label = STAGES[stage]
        config = InviteConfig(args.leader_aic, aics[stage], args.credentials_dir,
                              timeout=20, join_timeout=args.join_timeout, hold_seconds=0)
        state = read_json(output / "state.json")
        row = next(row for row in state["stages"] if row["stage"] == stage)
        live_path = stage_directory(output, row) / "live_record.json"

        def checkpoint(record):
            pending = live_path.with_suffix(".pending.json")
            pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(live_path)
            if record["events"]:
                print(label, record["events"][-1]["event"], record["groupId"], flush=True)

        release = lambda record: confirm_task_release(record, args.release_timeout)
        return await function(config, task, execute=True, partner_ready=True,
                              partner_commit=commits[stage], task_timeout=timeouts[stage],
                              checkpoint=checkpoint, task_release=release)

    async def prepare_resources(task, search_result, directory):
        if args.resource_proof:
            bundle = read_json(args.resource_proof)
            verify_cached_preparation(bundle, task, search_result, cache_dir=ROOT / "outputs/pdf-cache")
            write_json(directory / "resource_attempt.json", bundle)
            print("复用已核验资源证据；S1与PDF缓存校验通过，未重新请求资源。", flush=True)
            return bundle
        print("正在准备S2资源：核验已知论文PDF和仓库来源；不调用模型、不重跑S1。", flush=True)
        return await prepare_reader_resources(task, search_result,
            cache_dir=ROOT / "outputs/pdf-cache",
            checkpoint=lambda value: write_json(directory / "resource_attempt.json", value))

    print("【正式平台三 Agent 连续协作】三个独立Group顺序执行；每阶段人工放行且不自动重试。")
    print("记录：", output)
    try:
        cleanup_proof = (read_json(args.business_cleanup_proof)
                         if args.business_cleanup_proof else None)
        state = asyncio.run(execute_platform_chain(plan, output, runner,
            resource_preparer=prepare_resources, resume_unstarted=args.resume_unstarted,
            resume_terminal_failed=args.resume_terminal_failed,
            resume_business_completed=args.resume_business_completed,
            business_cleanup_proof=cleanup_proof))
    except KeyboardInterrupt:
        print("已中断；检查 state.json 与当前 Partner SQLite，禁止直接重发未确认阶段。")
        return 130
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,100}", str(error)) else type(error).__name__
        print("流程停止：", code, "；检查 state.json 和对应阶段记录，不自动重试。")
        return 1
    print("三阶段连续协作完成：", state["state"] == "completed")
    print("报告：", output / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
