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
import re
from uuid import uuid4

from aip_business_partner import validate_local_task
from business_execution import validate_business_result
from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input
from planner_contract import validate_plan
from repository_paper_context import comparison_valid
from platform_chain_resources import validate_preparation


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


def stage_directory(output, row):
    """Fresh local-preflight attempts keep all previous files at their old paths."""
    if row["stage"] not in ORDER:
        raise PlatformChainError("INVALID_STAGE_DIRECTORY")
    base = Path(output) / "stages" / row["stage"]
    attempt = row.get("attemptDirectory")
    if attempt is not None:
        if not isinstance(attempt, str) or not re.fullmatch(r"unstarted-[0-9a-f]{32}", attempt):
            raise PlatformChainError("INVALID_STAGE_DIRECTORY")
        base = base / attempt
    if not base.resolve().is_relative_to(Path(output).resolve()):
        raise PlatformChainError("INVALID_STAGE_DIRECTORY")
    return base


def stage_payload(output, row, original, results, task):
    """Restore the frozen derived input without invoking the resource preparer."""
    if "preparationSha256" not in row:
        return original
    if task["taskType"] != "paper_read":
        raise PlatformChainError("INVALID_RESOURCE_STAGE")
    bundle = read_json(stage_directory(output, row) / "input_resolution.json")
    if digest(bundle) != row["preparationSha256"]:
        raise PlatformChainError("FORMAL_RESOURCE_CHECKPOINT_CHANGED")
    return validate_preparation(bundle, original, results[task["dependsOn"][0]])


def unstarted_preflight(record):
    """Accept only records proving that no business Start was ever published.

    This covers both a local preflight failure and an invitation-only attempt that
    was deliberately abandoned before task release.  Group side effects may have
    occurred in the latter case, but a fresh Group is safe because the immutable
    evidence proves that no Partner business execution could have been requested.
    """
    if not isinstance(record, dict):
        return False
    no_start = (record.get("startAttempted") is False
                and record.get("startPublished") is False
                and type(record.get("taskCommandsSent")) is int
                and record["taskCommandsSent"] == 0
                and record.get("result") is None
                and record.get("protocolCompleted") is False
                and record.get("remoteStateUnknown") is False)
    if not no_start:
        return False
    false_flags = ("networkAttempted", "aclWriteAttempted", "groupCreationAttempted",
                   "groupCreated", "invitationPublishAttempted", "invitationPublished",
                   "partnerJoinConfirmed", "taskReleaseConfirmed")
    local_preflight = (all(record.get(key) is False for key in false_flags)
                       and record.get("offlineChecksPassed") is False
                       and (record.get("error") or {}).get("code")
                       == "READER_REQUIRES_ACCESSIBLE_PDF_URL")
    invitation_only = (
        all(record.get(key) is True for key in (
            "offlineChecksPassed", "networkAttempted", "aclWriteAttempted",
            "groupCreationAttempted", "groupCreated", "invitationPublishAttempted",
            "invitationPublished", "partnerJoinConfirmed", "cleanupAttempted",
            "cleanupSucceeded", "aclDeleted", "groupResourcesDeleted", "connectionClosed"))
        and record.get("cleanupErrors") == []
        and record.get("taskReleaseConfirmed") is False
        and record.get("completeAttempted") is False
        and record.get("completePublished") is False
        and record.get("resultValidated") is False
        and record.get("providerExecutionVerified") is False
        and (record.get("error") or {}).get("code") in (
            "TASK_RELEASE_TIMEOUT", "TASK_RELEASE_CANCELED"))
    join_timeout = (
        all(record.get(key) is True for key in (
            "offlineChecksPassed", "networkAttempted", "aclWriteAttempted",
            "groupCreationAttempted", "groupCreated", "invitationPublishAttempted",
            "invitationPublished", "cleanupAttempted", "cleanupSucceeded",
            "aclDeleted", "groupResourcesDeleted", "connectionClosed"))
        and record.get("partnerJoinConfirmed") is False
        and record.get("taskReleaseConfirmed") is False
        and record.get("completeAttempted") is False
        and record.get("completePublished") is False
        and record.get("resultValidated") is False
        and record.get("providerExecutionVerified") is False
        and record.get("cleanupErrors") == []
        and (record.get("error") or {}).get("code") == "PARTNER_JOIN_TIMEOUT")
    return local_preflight or invitation_only or join_timeout


def verify_unstarted_stage(output, row, original, results, task):
    directory = stage_directory(output, row)
    payload = stage_payload(output, row, original, results, task)
    saved_task = read_json(directory / "task.json")
    if saved_task != payload or digest(saved_task) != row["taskSha256"]:
        raise PlatformChainError("FORMAL_STAGE_TASK_CHANGED")
    if row.get("runnerAttempted") is False and row["state"] == "blocked_resources":
        return
    record = read_json(directory / "record.json")
    if digest(record) != row.get("recordSha256") or not unstarted_preflight(record):
        raise PlatformChainError("FORMAL_STAGE_NOT_PROVEN_UNSTARTED")
    live_path = directory / "live_record.json"
    if live_path.exists():
        live = read_json(live_path)
        if not unstarted_preflight(live) or any(live.get(key) != record.get(key)
                                               for key in ("groupId", "sessionId", "taskId")):
            raise PlatformChainError("FORMAL_STAGE_NOT_PROVEN_UNSTARTED")


def terminal_failed(record):
    """Require a conclusive Partner failure and complete cleanup before retry."""
    return (isinstance(record, dict)
            and record.get("partnerTerminalState") == "failed"
            and record.get("startAttempted") is True
            and record.get("startPublished") is True
            and type(record.get("taskCommandsSent")) is int
            and record["taskCommandsSent"] == 1
            and record.get("result") is None
            and record.get("resultValidated") is False
            and record.get("protocolCompleted") is False
            and record.get("remoteStateUnknown") is False
            and record.get("passed") is False
            and record.get("cleanupAttempted") is True
            and record.get("cleanupSucceeded") is True
            and record.get("aclDeleted") is True
            and record.get("groupResourcesDeleted") is True
            and record.get("connectionClosed") is True
            and record.get("cleanupErrors") == []
            and (record.get("error") or {}).get("code") == "PARTNER_FAILED")


def verify_terminal_failed_stage(output, row, original, results, task):
    directory = stage_directory(output, row)
    payload = stage_payload(output, row, original, results, task)
    saved_task = read_json(directory / "task.json")
    if saved_task != payload or digest(saved_task) != row["taskSha256"]:
        raise PlatformChainError("FORMAL_STAGE_TASK_CHANGED")
    record = read_json(directory / "record.json")
    if digest(record) != row.get("recordSha256") or not terminal_failed(record):
        raise PlatformChainError("FORMAL_STAGE_NOT_PROVEN_TERMINAL_FAILED")
    live_path = directory / "live_record.json"
    if live_path.exists():
        live = read_json(live_path)
        if not terminal_failed(live) or any(live.get(key) != record.get(key)
                                             for key in ("groupId", "sessionId", "taskId")):
            raise PlatformChainError("FORMAL_STAGE_NOT_PROVEN_TERMINAL_FAILED")


def verify_saved_stage(output, row, payload):
    directory = stage_directory(output, row)
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


async def execute_platform_chain(plan, output, runner, *, resource_preparer=None,
                                 resume_unstarted=False, resume_terminal_failed=False):
    """Execute or safely resume a three-stage chain using an injected runner.

    runner(stage_name, seven_field_task) must return the persisted Leader record
    for exactly one fresh formal Group task.  The core never calls a runner for a
    completed stage and never retries a remotely started failed/uncertain stage.
    An explicit resume_unstarted may replace a proven local preflight failure or
    an invitation-only attempt for which no business Start was ever published.
    An explicit resume_terminal_failed may replace only a conclusive Partner
    terminal failure after complete cleanup; unknown remote states stay blocked.
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
    for task in tasks:
        payload = bind_business_task(task, results)
        stage = task["taskType"]
        existing = next((row for row in state["stages"] if row["stage"] == stage), None)
        if existing:
            if existing["state"] == "completed":
                final_payload = stage_payload(output, existing, payload, results, task)
                _, result = verify_saved_stage(output, existing, final_payload)
                results[task["subtaskId"]] = result
                continue
            if not resume_unstarted and not resume_terminal_failed:
                raise PlatformChainError("FORMAL_STAGE_NOT_SAFE_TO_REPLAY_" + stage.upper())
            if resume_terminal_failed:
                try:
                    verify_terminal_failed_stage(output, existing, payload, results, task)
                except PlatformChainError:
                    if not resume_unstarted:
                        raise
                    verify_unstarted_stage(output, existing, payload, results, task)
            else:
                verify_unstarted_stage(output, existing, payload, results, task)

        row = {"stage": stage, "subtaskId": task["subtaskId"], "state": "preparing",
               "taskSha256": digest(payload), "attempts": 1, "runnerAttempted": False}
        if existing:
            row.update(attemptDirectory="unstarted-" + uuid4().hex,
                       attempts=existing.get("attempts", 1) + 1)
            state.setdefault("unstartedHistory", []).append(deepcopy(existing))
            state["stages"].remove(existing)
        directory = stage_directory(output, row)
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / "task.json", payload)
        write_json(directory / "bound_task.json", payload)
        state["stages"].append(row)
        state["state"] = "running"
        write_json(state_path, state)
        record = None
        try:
            if stage == "paper_read" and resource_preparer is not None:
                bundle = await resource_preparer(deepcopy(payload),
                    deepcopy(results[task["dependsOn"][0]]), directory)
                prepared = validate_preparation(bundle, payload, results[task["dependsOn"][0]])
                write_json(directory / "input_resolution.json", bundle)
                row["preparationSha256"] = digest(bundle)
                payload = prepared
                write_json(directory / "task.json", payload)
                row["taskSha256"] = digest(payload)
                write_json(state_path, state)
            row.update(state="running", runnerAttempted=True)
            write_json(state_path, state)
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
            blocked = "blocked_uncertain" if row["runnerAttempted"] else "blocked_resources"
            row.update(state=blocked, errorType=type(error).__name__)
            state["state"] = blocked
            write_json(state_path, state)
            (output / "report.md").write_text(render_report(state), encoding="utf-8")
            raise

    groups = [row["groupId"] for row in state["stages"]]
    tasks_ids = [row["taskId"] for row in state["stages"]]
    if len(set(groups)) != 3 or len(set(tasks_ids)) != 3:
        raise PlatformChainError("DISTINCT_FORMAL_GROUP_AND_TASK_IDS_REQUIRED")
    state["state"] = "completed"
    state["formalPlatformExecuted"] = all(
        read_json(stage_directory(output, row) / "record.json").get("scope", "").startswith("PLATFORM_")
        for row in state["stages"])
    state["automaticRetries"] = 0
    write_json(state_path, state)
    (output / "report.md").write_text(render_report(state), encoding="utf-8")
    return deepcopy(state)
