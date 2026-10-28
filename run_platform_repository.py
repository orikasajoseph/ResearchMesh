"""Leader single-task entry for the official Wutong Repository Group Partner."""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from group_invitation import InviteConfig
from group_literature_leader import run_group_repository, run_group_repository_chain, validate_repository_task
from platform_contextual_handoff import build_contextual_handoff
from platform_mq_check import ROOT
from run_platform_literature import confirm_task_release


def report_text(record):
    live = record["scope"] in ("PLATFORM_REPOSITORY_SINGLE_TASK", "PLATFORM_REPOSITORY_CHAIN_TASK")
    contextual = record["scope"] in ("PLATFORM_REPOSITORY_CHAIN_TASK", "OFFLINE_PLATFORM_REPOSITORY_CHAIN_PREFLIGHT")
    lines = ["# ResearchMesh 正式平台 Repository 单任务检查", "",
             "执行范围：" + ("正式 Group 单任务" if live else "离线预检，未发送任务"), "",
             f"Group ID：{record['groupId']}", f"Task ID：{record['taskId']}",
             f"Session ID：{record['sessionId']}", "",
             f"完成确认：{record['protocolCompleted']}；业务结果可用：{record['businessReady']}；"
             f"清理成功：{record['cleanupSucceeded']}；本轮通过：{record['passed']}", "",
             "Partner 由明确 Repository AIC 指定，本轮未使用 ADP 自动发现。Leader 不调用模型。",
             ("论文上下文 static 对照；标识符与词面线索不代表模型关联分析或代码复现。"
              if contextual else "指定 GitHub 仓库的 static 分析；禁止搜索仓库、执行目标代码。"),
             "真实 clone/CodeWiki 次数、SQLite 去重及实际执行版本需与吴侧 formal 审计对应核验。", ""]
    if record["error"]:
        lines.extend(["错误：" + record["error"]["code"], ""])
    if live:
        lines.extend([f"Start 已发布：{record.get('startPublished', False)}；"
                      f"Complete 已发布：{record.get('completePublished', False)}。",
                      "taskCommandsSent 统计 Start/Complete 协议命令，不是 clone、CodeWiki 或模型调用次数。", "",
                      "验收待人工核对：仓库 URL/commit、静态证据、Worker 执行次数、SQLite completed、"
                      "Result canonical SHA-256、DISBAND/GROUP_LEFT 及资源残留。", ""])
    if record.get("partnerFailure"):
        diagnostic = record["partnerFailure"]
        lines.extend(["Partner 业务错误码：" + (", ".join(diagnostic["codes"]) or "未提供可识别的安全错误码"),
                      "其他远端错误内容未保存；请用 Group/Session/Task ID 对照吴侧日志。", ""])
    if record["remoteStateUnknown"]:
        lines.extend(["远端执行结果不确定：禁止直接重发 Start；请先核对 Partner SQLite 任务记录。", ""])
    result = record["result"]
    if result is not None:
        lines.extend([result["summary"], ""])
        for item in result["items"]:
            data = item["data"]
            repository = data.get("repository") or {}
            overview = data.get("overview") or {}
            lines.extend(["## " + str(data.get("url") or repository.get("url") or "仓库分析"), "",
                          "Commit：" + str(repository.get("commitHash") or "未提供"), "",
                          "概览：" + str(overview.get("text") or "未提供"), "",
                          "依赖条数：" + str(len(data.get("dependencies") or [])), "",
                          "入口条数：" + str(len(data.get("entrypoints") or [])), "",
                          "复现障碍条数：" + str(len(data.get("reproductionBarriers") or [])), "",
                          "证据条数：" + str(len(data.get("evidence") or [])), ""])
        for warning in result["warnings"]:
            lines.extend(["提示：" + warning, ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leader-aic", required=True)
    parser.add_argument("--partner-aic", required=True)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--task", type=Path, default=ROOT / "examples/group_repository_single_task_v02.json")
    parser.add_argument("--context-chain", type=Path, help="已完成 S1 的连续链记录目录")
    parser.add_argument("--context-reader", type=Path, help="已完成的正式 Reader 单任务记录目录")
    parser.add_argument("--context-proof", type=Path, help="与 S1 绑定的 PDF/仓库来源证据")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--partner-ready", action="store_true")
    parser.add_argument("--partner-commit", help="Confirmed running Partner commit, recorded but not remotely verified")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--join-timeout", type=float, default=45)
    parser.add_argument("--task-timeout", type=float, default=300)
    parser.add_argument("--confirm-after-join", action="store_true",
                        help="Pause after GROUP_JOINED until SEND is typed")
    parser.add_argument("--release-timeout", type=float, default=1200)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    contextual = any((args.context_chain, args.context_reader, args.context_proof))
    if contextual and not all((args.context_chain, args.context_reader, args.context_proof)):
        print("论文上下文交接必须同时指定 --context-chain、--context-reader 和 --context-proof。")
        return 2
    if contextual and args.execute and not args.confirm_after_join:
        print("论文上下文正式任务必须指定 --confirm-after-join。")
        return 2
    try:
        if contextual:
            task, handoff = build_contextual_handoff(args.context_chain, args.context_reader, args.context_proof)
        else:
            task = validate_repository_task(json.loads(args.task.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError, KeyError):
        print("任务或上游交接证据无效；未连接平台，未发送任务。")
        return 2
    if args.confirm_after_join and not args.execute:
        print("--confirm-after-join 仅用于 --execute 正式运行。")
        return 2
    if not 1 <= args.release_timeout <= 3600:
        print("--release-timeout 必须为 1 至 3600 秒。")
        return 2
    if not 1 <= args.task_timeout <= 600:
        print("--task-timeout 必须为 1 至 600 秒；首轮 static Repository 建议 300 秒。")
        return 2
    directory = ROOT / "outputs/platform-repository" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    if contextual:
        (directory / "handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    config = InviteConfig(args.leader_aic, args.partner_aic, args.credentials_dir,
                          timeout=args.timeout, join_timeout=args.join_timeout, hold_seconds=0)
    last_event_count = 0

    def checkpoint(record):
        nonlocal last_event_count
        pending = directory / "record.pending.json"
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(directory / "record.json")
        for event in record["events"][last_event_count:]:
            print(event["event"], "Group:", record["groupId"], flush=True)
        last_event_count = len(record["events"])

    print("【正式平台 Repository 单任务】" if args.execute else "【仅离线预检】不连接平台，不发送任务。", flush=True)
    if args.execute:
        print("将发送一次指定仓库 static 分析；不搜索仓库、不执行目标代码、不调用 Leader 模型、不自动重试 Start。", flush=True)
    print("记录：", directory, flush=True)
    try:
        task_release = ((lambda record: confirm_task_release(record, args.release_timeout))
                        if args.confirm_after_join else None)
        runner = run_group_repository_chain if contextual else run_group_repository
        record = asyncio.run(runner(config, task, execute=args.execute,
            partner_ready=args.partner_ready, partner_commit=args.partner_commit,
            task_timeout=args.task_timeout, checkpoint=checkpoint,
            task_release=task_release))
    except KeyboardInterrupt:
        print("已中断；先检查本轮记录与 Partner SQLite 状态，不要直接重发任务。")
        return 130
    (directory / "report.md").write_text(report_text(record), encoding="utf-8")
    if record["result"] is not None:
        (directory / "result.json").write_text(json.dumps(record["result"], ensure_ascii=False, indent=2), encoding="utf-8")
    print("passed：", record["passed"], "；protocolCompleted：", record["protocolCompleted"],
          "；businessReady：", record["businessReady"], "；cleanupSucceeded：", record["cleanupSucceeded"])
    if record["error"]:
        print("错误：", record["error"]["code"])
    if record["remoteStateUnknown"]:
        print("远端状态不确定：请核对 Group/Session/Task ID，不要重发 Start。")
    print("报告：", directory / "report.md")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
