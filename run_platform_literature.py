"""Leader single-task entry for the official Wutong Literature Group Partner."""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from group_invitation import InviteConfig
from group_literature_leader import run_group_literature, validate_literature_task
from platform_mq_check import ROOT


def report_text(record):
    live = record["scope"] == "PLATFORM_LITERATURE_SINGLE_TASK"
    lines = ["# ResearchMesh 正式平台 Literature 单任务检查", "",
             "执行范围：" + ("正式 Group 单任务" if live else "离线预检，未发送任务"), "",
             f"Group ID：{record['groupId']}", f"Task ID：{record['taskId']}",
             f"Session ID：{record['sessionId']}", "",
             f"完成确认：{record['protocolCompleted']}；业务结果可用：{record['businessReady']}；"
             f"清理成功：{record['cleanupSucceeded']}；本轮通过：{record['passed']}", "",
             "Partner 由明确 AIC 指定，本轮未使用 ADP 自动发现。Leader 不调用模型。",
             "论文数据来自 Partner 返回值；真实 Provider 调用及后端版本需与吴侧日志对应核验。", ""]
    if record["error"]:
        lines.extend(["错误：" + record["error"]["code"], ""])
    if live:
        lines.extend(["验收待人工核对：返回论文是否为指定目标、吴侧真实 Provider 调用日志、"
                      "DISBAND 接收与离群日志。passed 仅表示本程序检查通过；"
                      "cleanupSucceeded 不代表已收到 Partner 离群确认。", ""])
    if record.get("partnerFailure"):
        diagnostic = record["partnerFailure"]
        lines.extend(["Partner 业务错误码：" + (", ".join(diagnostic["codes"]) or "未提供可识别的安全错误码"),
                      "其他远端错误内容未保存；请用 Group/Session/Task ID 对照吴侧日志。", ""])
    if record["remoteStateUnknown"]:
        lines.extend(["远端执行结果不确定：禁止直接重发 Start；请先核对 Partner 任务记录。", ""])
    result = record["result"]
    if result is not None:
        lines.extend([result["summary"], ""])
        if not result["items"]:
            lines.extend(["返回空检索结果；即使完成协议确认，也不计为首轮论文结果验收通过。", ""])
        for item in result["items"]:
            data = item["data"]
            lines.extend(["## " + data["title"], "", "出处：" + str(data.get("url") or data.get("doi") or data.get("pdfUrl")), ""])
        for warning in result["warnings"]:
            lines.extend(["提示：" + warning, ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leader-aic", required=True)
    parser.add_argument("--partner-aic", required=True)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--task", type=Path, default=ROOT / "examples/group_literature_single_task_v02.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--partner-ready", action="store_true")
    parser.add_argument("--partner-commit", help="Confirmed running Partner commit, recorded but not remotely verified")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--join-timeout", type=float, default=45)
    parser.add_argument("--task-timeout", type=float, default=180)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        task = validate_literature_task(json.loads(args.task.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError, KeyError):
        print("任务文件无效：需要七字段 Literature v0.2 Task，maxResults=1。")
        return 2
    directory = ROOT / "outputs/platform-literature" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
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

    print("【正式平台 Literature 单任务】" if args.execute else "【仅离线预检】不连接平台，不发送任务。", flush=True)
    if args.execute:
        print("将发送一次真实检索任务；不调用 Planner，不自动重试 Start；Partner 可能使用外部 API 额度。", flush=True)
    print("记录：", directory, flush=True)
    try:
        record = asyncio.run(run_group_literature(config, task, execute=args.execute,
            partner_ready=args.partner_ready, partner_commit=args.partner_commit,
            task_timeout=args.task_timeout, checkpoint=checkpoint))
    except KeyboardInterrupt:
        print("已中断；先检查本轮记录与 Partner 状态，不要直接重发任务。")
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
