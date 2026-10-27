"""Durable three-stage orchestration core for future formal platform runners.

This module performs no network, model, repository or platform work itself.  A
caller injects one async runner per stage.  The core binds real upstream
results, checkpoints every completed stage, and never automatically replays a
stage whose remote execution state is unknown.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from aip_business_partner import validate_local_task
from business_execution import validate_business_result
from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input
from planner_contract import validate_plan
from repository_paper_context import comparison_valid


SCOPE = "FORMAL_PLATFORM_THREE_AGENT_ORCHESTRATION_CORE_V1"
ORDER = ("literature_search", "paper_read", "repository_analyze")


class PlatformChainError(ValueError):
    pass


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return sha256(canonical_bytes(value)).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_chain(plan):
    validate_plan(plan)
    if plan["needsClarification"]:
        raise PlatformChainError("CHAIN_NEEDS_CLARIFICATION")
    tasks = plan["tasks"]
    if len(tasks) != 3 or tuple(task["taskType"] for task in tasks) != ORDER:
        raise PlatformChainError("EXACT_THREE_AGENT_CHAIN_REQUIRED")
    search, reader, repository = tasks
    if (search["dependsOn"] or reader["dependsOn"] != [search["subtaskId"]]
            or repository["dependsOn"] != [reader["subtaskId"]]):
        raise PlatformChainError("LINEAR_S1_S2_S3_DEPENDENCIES_REQUIRED")
    if (search["constraints"].get("maxResults") != 1
            or reader["constraints"].get("maxPapers") != 1
            or repository["constraints"].get("maxRepositories") != 1
            or repository["constraints"].get("executeCode") is not False
            or repository["constraints"].get("allowRepositorySearch") is not False
            or repository["constraints"].get("requirePaperNotes") is not True):
        raise PlatformChainError("FORMAL_CHAIN_SAFETY_CONSTRAINTS_REQUIRED")
    if reader["input"].get("papers") or repository["input"].get("papers") \
            or repository["input"].get("paperNotes"):
        raise PlatformChainError("DEPENDENT_INPUT_MUST_BE_BOUND_FROM_RESULTS")
    return deepcopy(tasks)


def bind_business_task(task, dependency_results):
    bound = bind_task_input(task, dependency_results)
    payload = {key: deepcopy(bound[key]) for key in BUSINESS_TASK_FIELDS}
    return validate_local_task(payload)


def validate_completed_record(record, payload):
    if not isinstance(record, dict) or record.get("task") != payload:
        raise PlatformChainError("FORMAL_STAGE_TASK_MISMATCH")
    if not all(record.get(key) is True for key in
               ("passed", "protocolCompleted", "businessReady", "resultValidated", "cleanupSucceeded")):
        raise PlatformChainError("FORMAL_STAGE_NOT_ACCEPTED")
    if record.get("remoteStateUnknown") is True or record.get("error") is not None:
        raise PlatformChainError("FORMAL_STAGE_REMOTE_STATE_UNCERTAIN")
    for key in ("groupId", "sessionId", "taskId", "partnerAic"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise PlatformChainError("FORMAL_STAGE_IDENTITY_MISSING")
    result = deepcopy(record.get("result"))
    validate_business_result(payload, result, repository_analysis_mode="static")
    if payload["taskType"] == "repository_analyze" \
            and payload["constraints"].get("requirePaperNotes") is True \
            and not comparison_valid(result, payload):
        raise PlatformChainError("FORMAL_REPOSITORY_COMPARISON_INVALID")
    return result


def verify_saved_stage(output, row, payload):
    directory = output / "stages" / row["stage"]
    task_path = directory / "task.json"
    record_path, result_path = directory / "record.json", directory / "result.json"
    if not task_path.is_file() or not record_path.is_file() or not result_path.is_file():
        raise PlatformChainError("FORMAL_STAGE_CHECKPOINT_MISSING")
    if digest(read_json(task_path)) != row["taskSha256"] or read_json(task_path) != payload:
        raise PlatformChainError("FORMAL_STAGE_TASK_CHANGED")
    record, result = read_json(record_path), read_json(result_path)
    if digest(record) != row["recordSha256"] or digest(result) != row["resultSha256"]:
        raise PlatformChainError("FORMAL_STAGE_CHECKPOINT_CHANGED")
    if validate_completed_record(record, payload) != result:
        raise PlatformChainError("FORMAL_STAGE_RESULT_CHANGED")
    return record, result


def render_report(state):
    lines = ["# ResearchMesh 正式平台三 Agent 连续编排记录", "",
             "> 编排核心本身不执行网络或模型；是否为正式平台结果由注入的 runner 及每阶段记录决定。",
             "> 任一已启动阶段失败或状态不确定时不会自动重放。", "",
             "研究问题：" + state["userQuery"], "",
             "总体状态：" + state["state"], "",
             "| 阶段 | Group | Task | Result SHA-256 | 状态 |",
             "|---|---|---|---|---|"]
    for row in state["stages"]:
        lines.append("| " + " | ".join((row["stage"], row.get("groupId", "-"),
            row.get("taskId", "-"), row.get("resultSha256", "-"), row["state"])) + " |")
    lines += ["", "验收边界：" + ("三个阶段已由同一持久编排顺序完成。" if state["state"] == "completed"
                                    else "尚未完成全部阶段，不能作为三 Agent 连续协作通过证明。"), ""]
    return "\n".join(lines)


async def execute_platform_chain(plan, output, runner):
    """Execute or safely resume a three-stage chain using an injected runner.

    runner(stage_name, seven_field_task) must return the persisted Leader record
    for exactly one fresh formal Group task.  The core never calls a runner for a
    completed stage and never retries a failed/uncertain stage.
    """
    if not callable(runner):
        raise PlatformChainError("FORMAL_STAGE_RUNNER_REQUIRED")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tasks = validate_chain(deepcopy(plan))
    plan_hash = digest(plan)
    state_path = output / "state.json"
    if state_path.exists():
        state = read_json(state_path)
        if state.get("scope") != SCOPE or state.get("planSha256") != plan_hash:
            raise PlatformChainError("FORMAL_CHAIN_PLAN_CHANGED")
    else:
        state = {"scope": SCOPE, "userQuery": plan["userQuery"], "planSha256": plan_hash,
                 "state": "running", "stages": [], "results": {},
                 "automaticRetries": 0, "formalPlatformExecuted": None}
        write_json(output / "plan.json", plan)
        write_json(state_path, state)
    if digest(read_json(output / "plan.json")) != plan_hash:
        raise PlatformChainError("FORMAL_CHAIN_PLAN_CHANGED")

    results = {}
    for index, task in enumerate(tasks):
        payload = bind_business_task(task, results)
        stage = task["taskType"]
        existing = next((row for row in state["stages"] if row["stage"] == stage), None)
        if existing:
            if existing["state"] != "completed":
                raise PlatformChainError("FORMAL_STAGE_NOT_SAFE_TO_REPLAY_" + stage.upper())
            _, result = verify_saved_stage(output, existing, payload)
            results[task["subtaskId"]] = result
            continue

        directory = output / "stages" / stage
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / "task.json", payload)
        row = {"stage": stage, "subtaskId": task["subtaskId"], "state": "running",
               "taskSha256": digest(payload), "attempts": 1}
        state["stages"].append(row)
        write_json(state_path, state)
        record = None
        try:
            record = await runner(stage, deepcopy(payload))
            if isinstance(record, dict):
                write_json(directory / "record.json", record)
                row["recordSha256"] = digest(record)
                write_json(state_path, state)
            result = validate_completed_record(record, payload)
            write_json(directory / "result.json", result)
            row.update(state="completed", groupId=record["groupId"],
                       sessionId=record["sessionId"], taskId=record["taskId"],
                       partnerAic=record["partnerAic"],
                       resultSha256=digest(result))
            results[task["subtaskId"]] = result
            state["results"][task["subtaskId"]] = {"stage": stage,
                "resultSha256": row["resultSha256"], "items": len(result["items"])}
            write_json(state_path, state)
        except Exception as error:
            row.update(state="blocked_uncertain", errorType=type(error).__name__)
            state["state"] = "blocked_uncertain"
            write_json(state_path, state)
            (output / "report.md").write_text(render_report(state), encoding="utf-8")
            raise

    groups = [row["groupId"] for row in state["stages"]]
    tasks_ids = [row["taskId"] for row in state["stages"]]
    if len(set(groups)) != 3 or len(set(tasks_ids)) != 3:
        raise PlatformChainError("DISTINCT_FORMAL_GROUP_AND_TASK_IDS_REQUIRED")
    state["state"] = "completed"
    state["formalPlatformExecuted"] = all(
        read_json(output / "stages" / row["stage"] / "record.json").get("scope", "").startswith("PLATFORM_")
        for row in state["stages"])
    state["automaticRetries"] = 0
    write_json(state_path, state)
    (output / "report.md").write_text(render_report(state), encoding="utf-8")
    return deepcopy(state)
