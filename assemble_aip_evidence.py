"""Assemble and revalidate ALREADY EXECUTED local AIP search/read checkpoints.

No Planner, Provider, downloads, task replay or network. Not a newly executed
end-to-end or platform discovery run; retain original task IDs and timestamps.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from aip_reader_inputs import prepare_reader_input, read_json
from input_binding import paper_ref
from reader_adapter import ReaderAdapterError, normalize_reader_result
from run_aip_literature import assess_live_run
from run_aip_reader import assess_reader_run

ROOT = Path(__file__).resolve().parent


def check_reader_wire(outcome, payload):
    execution, rows = outcome["execution"], outcome["aipMessages"]
    commands = [r["command"]["command"] for r in rows]
    if (commands[0] != "start" or commands[-1] != "complete"
            or any(c != "get" for c in commands[1:-1])
            or rows[0]["command"]["dataItems"] != [{"type": "data", "data": payload}]
            or rows[-1]["response"]["result"]["status"]["state"] != "completed"):
        raise ReaderAdapterError("INVALID_READER_AIP_CHECKPOINT")
    session = rows[0]["command"]["sessionId"]
    for row in rows:
        request, response = row["command"], row["response"]["result"]
        if (request["taskId"] != execution["taskId"] or response["taskId"] != execution["taskId"]
                or request["sessionId"] != session or response["sessionId"] != session
                or response["senderId"] != execution["agentAic"] or response["senderRole"] != "partner"):
            raise ReaderAdapterError("INVALID_READER_AIP_CHECKPOINT")


def assemble(search_dir, reader_dir, *, cache_dir, planned_repository=False):
    search_dir, reader_dir = Path(search_dir).resolve(), Path(reader_dir).resolve()
    saved_input = read_json(reader_dir / "reader_input.json")
    if Path(saved_input["searchSource"]).resolve() != search_dir:
        raise ReaderAdapterError("SEARCH_READER_CHECKPOINT_MISMATCH")
    prepared = prepare_reader_input(search_dir, saved_input["pdfProofSource"], cache_dir=cache_dir,
                                   subtask_id=saved_input["payload"]["subtaskId"])
    if prepared != saved_input:
        raise ReaderAdapterError("SEARCH_READER_CHECKPOINT_MISMATCH")
    task = prepared["payload"]
    search = read_json(search_dir / "outcome.json")
    backend = read_json(search_dir / "backend_outcome.json")
    reader = read_json(reader_dir / "outcome.json")
    journal = read_json(reader_dir / "reader_journal.json")
    if journal.get("pdf") != prepared["pdf"] or not assess_reader_run(reader, journal, task)["passed"]:
        raise ReaderAdapterError("INVALID_READER_CHECKPOINT")
    check_reader_wire(reader, task)
    normalized = normalize_reader_result(task, reader["execution"]["result"])
    plan = read_json(search_dir / "plan.json")
    prefix = [prepared["searchSubtaskId"], task["subtaskId"]]
    ids = [t["subtaskId"] for t in plan["tasks"]]
    allowed = ids == prefix
    if planned_repository:
        allowed = (len(ids) == 3 and ids[:2] == prefix
                   and plan["tasks"][2]["taskType"] == "repository_analyze"
                   and task["subtaskId"] in plan["tasks"][2]["dependsOn"])
    if not allowed:
        raise ReaderAdapterError("ONLY_COMPLETE_TWO_TASK_PLAN_SUPPORTED")
    if search["execution"]["taskId"] == reader["execution"]["taskId"]:
        raise ReaderAdapterError("DISTINCT_AIP_TASK_IDS_REQUIRED")
    tasks = {t["subtaskId"]: t for t in plan["tasks"]}
    records = []
    for directory, outcome, raw in ((search_dir, search, read_json(search_dir / "payload.json")),
                                    (reader_dir, reader, task)):
        execution, rows = outcome["execution"], outcome["aipMessages"]
        subtask = execution["subtaskId"]
        completed_event = execution["events"][-1]
        if completed_event["command"] != "complete" or completed_event["state"] != "completed":
            raise ReaderAdapterError("COMPLETION_OBSERVATION_REQUIRED")
        records.append({"subtaskId": subtask, "requiredSkill": tasks[subtask]["requiredSkill"],
            "dependsOn": tasks[subtask]["dependsOn"], "agentAic": execution["agentAic"],
            "aipTaskId": execution["taskId"], "status": execution["state"],
            "startedAt": rows[0]["command"]["sentAt"], "finishedAt": completed_event["at"],
            "timingSource": "Leader start command and observed completed response",
            "partnerEnvelopeSentAt": rows[-1]["response"]["result"]["sentAt"],
            "latencyMs": execution["latencyMs"], "warnings": execution["result"]["warnings"], "error": outcome["error"],
            "evidence": [e for item in execution["result"]["items"] for e in item["data"].get("evidence", [])],
            "payload": deepcopy(raw), "result": deepcopy(execution["result"]), "recordDirectory": str(directory)})
    notes = normalized["result"]["items"]
    papers = [paper_ref(item["data"]["source"]) for item in notes]
    urls = list(dict.fromkeys(url for paper in papers for url in paper["repositoryUrls"]))
    # Only pass explicit structured upstream URLs; do not infer repositories from model prose.
    handoff = {"papers": papers, "paperNotes": [deepcopy(item["data"]) for item in notes], "repositoryUrls": urls}
    manifests = []
    for directory, names in ((search_dir, ("plan.json", "payload.json", "outcome.json", "backend_outcome.json")),
                             (reader_dir, ("reader_input.json", "reader_journal.json", "outcome.json"))):
        for name in names:
            path = directory / name
            manifests.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"scope": "ASSEMBLED_PREVIOUS_REAL_LOCAL_AIP_RUNS_NO_NEW_EXECUTION",
        "assembledAt": datetime.now().astimezone().isoformat(), "userQuery": plan["userQuery"], "plan": plan,
        "executionRecords": records, "results": [r["result"] for r in records], "sourceManifest": manifests,
        "repositoryHandoff": {"input": handoff, "taskExecuted": False, "taskPlannedInThisRun": False,
                              "hasExplicitRepositoryUrl": bool(urls),
                              "blockers": ["REPOSITORY_AGENT_NOT_VALIDATED"] + ([] if urls else ["REPOSITORY_URL_REQUIRED"])},
        "acceptance": {"passed": True, "bothOriginalAipTasksCompleted": True,
            "searchBusinessReady": assess_live_run(search, backend)["businessReady"], "readerBusinessReady": True,
            "searchResultBoundToReader": True, "allTasksInSavedPlanAccountedFor": not planned_repository,
            "newModelCalls": 0, "newPaperApiCalls": 0, "newPdfDownloads": 0, "newAipTaskCalls": 0,
            "platformDiscoveryUsed": False, "tlsIdentityVerified": False, "repositoryAnalysisExecuted": False,
            "singleFreshEndToEndRun": False, "scientificClaimsHumanVerified": False,
            "sumOriginalTaskLatencyMs": round(sum(r["latencyMs"] for r in records), 3),
            "latencyMetricMeaning": "sum of two original task durations, not end-to-end or network-only latency"}}


def render(record):
    pipeline = record.get("pipeline", {})
    if (pipeline.get("scope") == "LOCAL_AIP_SINGLE_ENTRY_SEARCH_READER"
            and pipeline.get("singleEntryCompleted") is True):
        planning = ("本流程包含真实模型规划。" if pipeline.get("plannerExecutedByPipeline")
                    else "本流程复用已保存的计划，没有再次调用 Planner。")
        lines = ["# ResearchMesh 本机 AIP 科研演示报告", "",
            "> 单入口检索与全文阅读链路已完成；各阶段的真实调用及结果见下表和原始记录。",
            "> " + planning + " 检索和阅读可能计费；模型结论待人工核验。",
            "> 未包含 ADP、梧桐发现、证书身份或 Repository Agent；不是正式平台端到端验收。", ""]
    else:
        lines = ["# ResearchMesh 真实本机 AIP 检索与阅读：证据汇总", "",
            "> 本报告复核并组合两次已经执行成功的本机 AIP 记录，没有重新执行科研任务。",
            "> 不是一次新端到端运行，不包含 ADP、梧桐发现、证书或 Repository Agent 验收。", ""]
    lines += [
        "研究问题：" + record["userQuery"], "",
        "| 子任务 | skill | AIC | AIP taskId | 协议状态 | 原任务耗时 ms |",
        "|---|---|---|---|---|---:|"]
    for row in record["executionRecords"]:
        lines.append(f"| {row['subtaskId']} | {row['requiredSkill']} | {row['agentAic']} | {row['aipTaskId']} | {row['status']} | {row['latencyMs']} |")
    for row in record["executionRecords"]:
        lines += ["", "## " + row["subtaskId"], "", row["result"]["summary"], "",
                  "原始记录：" + row["recordDirectory"], ""]
        for item in row["result"]["items"]:
            data = item["data"]
            if item["kind"] == "paper":
                lines.append("- " + data["title"] + "；出处：" + (data.get("url") or data.get("doi") or "未提供"))
            else:
                lines += ["### " + data["source"]["title"], "", "方法：" + data["method"], "",
                          "发现：" + data["findings"], "", "局限：" + data["limitations"], "", "原文证据（待人工核验）：", ""]
                for evidence in data["evidence"]:
                    lines += ["- 位置：" + str(evidence["locator"]) + "；来源：" + evidence["url"],
                              "  支持结论：" + evidence["supports"], ""]
    lines += ["", "## 尚待联调", "", "ADP 注册/发现、证书和跨电脑访问未验收。",
              "S3 输入已整理在 research_run.json 的 repositoryHandoff；本计划未要求 S3，也未调用仓库 Agent。",
              "不从模型描述中猜测仓库地址；当前是否有结构化仓库地址：" + str(record["repositoryHandoff"]["hasExplicitRepositoryUrl"]), ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", type=Path, required=True)
    parser.add_argument("--reader", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = assemble(args.search, args.reader, cache_dir=ROOT / "outputs/pdf-cache")
    except (Exception, KeyboardInterrupt) as error:
        print("证据汇总未完成：", error.code if isinstance(error, ReaderAdapterError) else "INVALID_SAVED_CHECKPOINT")
        return 1
    output = ROOT / "outputs" / datetime.now().strftime("aip-evidence-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    (output / "research_run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text(render(record), encoding="utf-8")
    print("已复核并汇总两项真实本机 AIP 结果；没有新模型、检索或 AIP 调用。")
    print("未包含平台发现、证书或 S3；本次是旧记录汇总，不是新端到端执行。")
    print("记录：", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
