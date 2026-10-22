"""Leader 单篇 Reader 业务适配；不执行发现或构造 AIP 消息。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import sys

from common.models import BusinessTask, PlannerTask, BUSINESS_TASK_FIELDS
from input_binding import paper_ref, validate_task_input
from result_contract import validate_result


class ReaderAdapterError(ValueError):
    """静态错误码，避免回显 Provider 异常中的配置或凭据。"""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def to_reader_request(payload):
    """接受已绑定论文的 Planner/业务任务；本阶段明确只处理单篇。"""
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
        if task["constraints"].get("maxPapers", 1) != 1 or len(task["input"]["papers"]) != 1:
            raise ReaderAdapterError("SINGLE_PAPER_REQUIRED")
        request = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
        request["constraints"] = {"maxPapers": 1}
        request["input"] = {"papers": [paper_ref(task["input"]["papers"][0])]}
        return request
    except ReaderAdapterError:
        raise
    except (ValueError, TypeError, KeyError):
        raise ReaderAdapterError("INVALID_TASK") from None


def normalize_reader_result(request, raw):
    """结构通过与阅读完成分别记录；返回值不是 AIP TaskResult。"""
    request = to_reader_request(request)
    result = deepcopy(raw)
    try:
        validate_result(result, request)
        if len(result["items"]) > 1:
            raise ValueError("multiple notes for single paper")
        if result["items"] and "FULL_TEXT_UNAVAILABLE" in result["warnings"]:
            raise ValueError("full text unavailable contradicts returned paper note")
        expected = request["input"]["papers"][0]
        for item in result["items"]:
            data = item["data"]
            if data.get("isMock") is True:
                raise ValueError("mock result cannot claim reader completion")
            if paper_ref(data["source"]) != expected:
                raise ValueError("source mismatch")
            for evidence in data["evidence"]:
                if evidence["sourceId"] != expected["paperId"]:
                    raise ValueError("evidence source mismatch")
                if evidence["url"] != expected["pdfUrl"]:
                    raise ValueError("evidence PDF mismatch")
    except (ValueError, TypeError, KeyError):
        raise ReaderAdapterError("INVALID_READER_RESULT") from None
    diagnostics = {"paperId": expected["paperId"], "structureValid": True,
                   "businessCompleted": bool(result["items"]),
                   "returnedCount": len(result["items"])}
    if not result["items"]:
        code = "FULL_TEXT_UNAVAILABLE" if "FULL_TEXT_UNAVAILABLE" in result["warnings"] else "READER_EMPTY_RESULT"
        diagnostics["outcome"] = "unavailable" if code == "FULL_TEXT_UNAVAILABLE" else "empty"
        return {"result": result, "error": {"code": code}, "diagnostics": diagnostics}
    diagnostics["outcome"] = "paper_note"
    diagnostics["analysisLevel"] = result["items"][0]["data"]["analysisLevel"]
    return {"result": result, "error": None, "diagnostics": diagnostics}


async def call_reader(payload, agent, task_model, *, timeout_seconds=45):
    request = to_reader_request(payload)
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ReaderAdapterError("INVALID_TIMEOUT")
    try:
        task = task_model.model_validate(request)
    except Exception:
        raise ReaderAdapterError("INVALID_READER_TASK") from None
    try:
        response = await asyncio.wait_for(agent.run(task), timeout_seconds)
        raw = response.model_dump(mode="json")
    except asyncio.TimeoutError:
        return _failed(request, "READER_TIMEOUT")
    except Exception:
        return _failed(request, "READER_CALL_FAILED")
    try:
        return normalize_reader_result(request, raw)
    except ReaderAdapterError as exc:
        return _failed(request, exc.code)


def _failed(request, code):
    return {"result": None, "error": {"code": code},
            "diagnostics": {"paperId": request["input"]["papers"][0]["paperId"],
                            "businessCompleted": False, "outcome": "failed"}}


def load_reader(root):
    """只加载显式指定的队友目录，不复制源码或自动安装 PaperQA。"""
    package = Path(root).resolve() / "researchmesh_paper_reader"
    init = package / "__init__.py"
    if not init.is_file():
        raise ReaderAdapterError("READER_ROOT_NOT_FOUND")
    name = "_researchmesh_zhang_paper_reader"
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(existing.__file__).resolve() != init:
            raise ReaderAdapterError("READER_ALREADY_LOADED_FROM_OTHER_ROOT")
        return existing
    spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
        raise ReaderAdapterError("READER_IMPORT_FAILED") from None
    return module
