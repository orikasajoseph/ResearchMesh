"""Leader 多篇 Reader 适配：顺序单篇调用、失败隔离和原任务汇总。"""
from __future__ import annotations

from copy import deepcopy
import math

from common.models import BusinessTask, PlannerTask, BUSINESS_TASK_FIELDS
from input_binding import paper_ref, validate_task_input
from reader_adapter import ReaderAdapterError, call_reader, normalize_reader_result
from result_contract import validate_result


def prepare_reader_batch(payload):
    """只接受已经绑定论文的任务，不截取超限输入或重复阅读同一论文。"""
    try:
        model = PlannerTask if isinstance(payload, dict) and (
            {"requiredSkill", "dependsOn"} & payload.keys()
        ) else BusinessTask
        task = model.model_validate(payload).model_dump()
        if task["taskType"] != "paper_read" or task["outputSchema"] != "paper_notes.v1":
            raise ReaderAdapterError("UNSUPPORTED_TASK")
        if model is PlannerTask and task["requiredSkill"] != "paper.analyze":
            raise ReaderAdapterError("UNSUPPORTED_SKILL")
        validate_task_input(task, dispatch=True)
        if set(task["constraints"]) - {"maxPapers"}:
            raise ReaderAdapterError("UNSUPPORTED_CONSTRAINT")
        papers = [paper_ref(paper) for paper in task["input"]["papers"]]
        limit = task["constraints"].get("maxPapers", 3)
        if len(papers) > limit:
            raise ReaderAdapterError("TOO_MANY_PAPERS")
        ids, locators = set(), set()
        for paper in papers:
            locator = paper["doi"] or paper["url"] or paper["pdfUrl"]
            if paper["paperId"] in ids or locator in locators:
                raise ReaderAdapterError("DUPLICATE_PAPER")
            ids.add(paper["paperId"])
            locators.add(locator)
        parent = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
        parent["constraints"] = {"maxPapers": limit}
        parent["input"] = {"papers": papers}
        children = []
        for index, paper in enumerate(papers, 1):
            child = deepcopy(parent)
            child["subtaskId"] = f"{parent['subtaskId']}/paper-{index:03d}"
            child["input"] = {"papers": [deepcopy(paper)]}
            child["constraints"] = {"maxPapers": 1}
            children.append(child)
        return parent, children
    except ReaderAdapterError:
        raise
    except (ValueError, TypeError, KeyError):
        raise ReaderAdapterError("INVALID_TASK") from None


def validate_reader_checkpoint(children, saved_outcomes):
    """返回重新校验的成功结果；失败记录不复用，也不信任旧诊断。"""
    # 仅显式续跑可复用成功记录。重验子任务、论文来源和证据，不信任历史诊断。
    cached = {}
    if saved_outcomes is not None:
        try:
            if not isinstance(saved_outcomes, list):
                raise ValueError()
            by_id = {child["subtaskId"]: child for child in children}
            seen = set()
            for saved in saved_outcomes:
                if not isinstance(saved, dict) or type(saved.get("succeeded")) is not bool:
                    raise ValueError()
                child = by_id[saved["subtaskId"]]
                if saved["subtaskId"] in seen:
                    raise ValueError()
                seen.add(saved["subtaskId"])
                if saved["paperId"] != child["input"]["papers"][0]["paperId"]:
                    raise ValueError()
                if saved["succeeded"] is False:
                    if not isinstance(saved.get("error"), dict) or not saved["error"].get("code"):
                        raise ValueError()
                    continue
                if saved.get("error") is not None:
                    raise ValueError()
                normalized = normalize_reader_result(child, saved["result"])
                if normalized["error"] is not None:
                    raise ValueError()
                cached[child["subtaskId"]] = normalized
        except (ValueError, TypeError, KeyError):
            raise ReaderAdapterError("INVALID_READER_CHECKPOINT") from None
    return cached


async def call_reader_batch(payload, agent, task_model, *, timeout_seconds=45,
                            saved_outcomes=None, on_paper=None, on_start=None):
    """每篇单独超时，无重试；取消向上传播，不伪装成已完成汇总。"""
    parent, children = prepare_reader_batch(payload)
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ReaderAdapterError("INVALID_TIMEOUT")
    # 全批预检后才调用，避免因后面的输入无效而先消耗前面的模型请求。
    try:
        for child in children:
            task_model.model_validate(child)
    except Exception:
        raise ReaderAdapterError("INVALID_READER_TASK") from None
    cached = validate_reader_checkpoint(children, saved_outcomes)
    items, warnings, records = [], [], []
    for child in children:
        reused = child["subtaskId"] in cached
        if reused:
            outcome = deepcopy(cached[child["subtaskId"]])
        else:
            if on_start is not None:
                on_start(deepcopy(child))
            try:
                outcome = await call_reader(child, agent, task_model, timeout_seconds=timeout_seconds)
            except ReaderAdapterError as exc:
                outcome = {"result": None, "error": {"code": exc.code},
                           "diagnostics": {"businessCompleted": False, "outcome": "failed"}}
        complete = outcome["error"] is None and outcome["diagnostics"]["businessCompleted"]
        record = {"paperId": child["input"]["papers"][0]["paperId"],
                  "subtaskId": child["subtaskId"], "succeeded": complete, **deepcopy(outcome)}
        if saved_outcomes is not None:
            record["reused"] = reused
        records.append(record)
        if on_paper is not None:
            on_paper(deepcopy(record))
        if complete:
            items.extend(deepcopy(outcome["result"]["items"]))
        if outcome["result"] is not None:
            warnings.extend(f"{child['subtaskId']}: {warning}" for warning in outcome["result"]["warnings"])
        if not complete:
            warnings.append(f"{child['subtaskId']}: {outcome['error']['code']}")
    completed = sum(record["succeeded"] for record in records)
    failed = len(children) - completed
    state = "success" if failed == 0 else "partial" if completed else "failed"
    result = {"schemaVersion": parent["schemaVersion"], "subtaskId": parent["subtaskId"],
              "taskType": "paper_read", "summary": f"已完成 {completed}/{len(children)} 篇论文阅读；失败 {failed} 篇。",
              "items": items, "warnings": list(dict.fromkeys(warnings))}
    validate_result(result, parent)
    error = None if failed == 0 else {"code": "PARTIAL_READER_FAILURE" if completed else "ALL_READERS_FAILED"}
    diagnostics = {"outcome": state, "businessCompleted": failed == 0,
                   "requestedCount": len(children), "completedCount": completed, "failedCount": failed}
    if saved_outcomes is not None:
        diagnostics["reusedCount"] = len(cached)
    return {"result": result, "error": error, "diagnostics": diagnostics, "paperOutcomes": records}
