"""张的 Literature Wrapper 与 ResearchMesh v0.2 的纯业务适配。

不构造 AIP 信封，不进行 ADP 发现。Wrapper 的 status 只在此边界消费。
保留原始交付包，通过依赖注入或显式路径加载，不复制凭据或修改队友源码。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from common.models import BusinessTask
from input_binding import github_repository_url, paper_ref, validate_task_input, web_url
from result_contract import validate_result


class AdapterError(ValueError):
    """Only static safe codes, never provider exception bodies or credentials."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Author(_Contract):
    authorId: StrictStr | None = None
    name: StrictStr


class _Paper(_Contract):
    paperId: StrictStr
    title: StrictStr | None = None
    authors: list[_Author] = Field(default_factory=list)
    year: StrictInt | None = None
    abstract: StrictStr | None = None
    doi: StrictStr | None = None
    url: StrictStr | None = None
    pdfUrl: StrictStr | None = None
    repositoryUrls: list[StrictStr] = Field(default_factory=list)
    citationCount: StrictInt | None = None


class _Metrics(_Contract):
    durationMs: float = Field(ge=0, allow_inf_nan=False)
    attempts: StrictInt = Field(ge=0)
    cacheHit: StrictBool


class _Error(_Contract):
    code: StrictStr
    message: StrictStr
    retryable: StrictBool = False


class _Response(_Contract):
    schemaVersion: Literal["paper_records.v1"]
    requestId: StrictStr
    status: Literal["success", "failure"]
    papers: list[_Paper] = Field(default_factory=list)
    total: StrictInt = Field(default=0, ge=0)
    provider: Literal["semantic-scholar"] = "semantic-scholar"
    metrics: _Metrics
    error: _Error | None = None


def to_wrapper_request(payload, request_id):
    """v0.2 business task -> literature_search.v0.1. Reject ignored constraints."""
    try:
        task = BusinessTask.model_validate(payload).model_dump()
        if task["taskType"] != "literature_search" or task["outputSchema"] != "paper_records.v1":
            raise AdapterError("UNSUPPORTED_TASK")
        validate_task_input(task, dispatch=True)
        constraints = task["constraints"]
        if set(constraints) - {"yearFrom", "yearTo", "maxResults"}:
            raise AdapterError("UNSUPPORTED_CONSTRAINT")
        if not isinstance(request_id, str) or not request_id.strip():
            raise AdapterError("INVALID_REQUEST_ID")
        start, end = constraints.get("yearFrom"), constraints.get("yearTo")
        year = None
        if start is not None or end is not None:
            year = str(start) if start == end else f"{start if start is not None else ''}:{end if end is not None else ''}"
        return {"schemaVersion": "literature_search.v0.1", "requestId": request_id,
                "query": task["input"]["query"], "filters": {"year": year},
                "limit": constraints.get("maxResults", 5)}
    except AdapterError:
        raise
    except (ValueError, KeyError, TypeError):
        raise AdapterError("INVALID_TASK") from None


def normalize_response(payload, request, raw):
    """Return {result,error,diagnostics}; this is NOT an AIP TaskResult or state."""
    expected = to_wrapper_request(payload, request.get("requestId"))
    if expected != request:
        raise AdapterError("REQUEST_TASK_MISMATCH")
    try:
        response = _Response.model_validate(raw)
    except ValueError:
        raise AdapterError("INVALID_WRAPPER_RESULT") from None
    if response.requestId != request["requestId"]:
        raise AdapterError("REQUEST_ID_MISMATCH")
    if response.total < len(response.papers):
        raise AdapterError("INCONSISTENT_TOTAL")
    diagnostics = {"provider": response.provider, "requestId": response.requestId,
                   "metrics": response.metrics.model_dump(), "providerTotal": response.total,
                   "providerReturned": len(response.papers)}
    result = {"schemaVersion": "0.2", "subtaskId": payload["subtaskId"],
              "taskType": "literature_search", "summary": "", "items": [], "warnings": []}
    if response.status == "failure":
        if response.error is None or response.papers or response.total:
            raise AdapterError("INCONSISTENT_FAILURE")
        if response.error.code == "NO_RESULTS":
            result["summary"] = "文献服务未返回匹配论文；不表示相关研究不存在。"
            result["warnings"] = ["NO_RESULTS：空检索结果，不生成虚构论文。"]
            diagnostics["outcome"] = "empty"
            return {"result": result, "error": None, "diagnostics": diagnostics}
        allowed = {"RATE_LIMITED", "INVALID_REQUEST", "PROVIDER_UNAVAILABLE"}
        code = response.error.code if response.error.code in allowed else "UNKNOWN_PROVIDER_ERROR"
        return {"result": None, "error": {"code": code, "retryable": response.error.retryable},
                "diagnostics": diagnostics}
    if response.error is not None or not response.papers:
        raise AdapterError("INCONSISTENT_SUCCESS")
    seen = set()
    dropped = 0
    converted_links = False
    for record in response.papers:
        data = record.model_dump()
        urls = data.pop("repositoryUrls")
        # 张包中的 repositoryUrls 是论文存储库地址，不是代码仓库。
        data["repositoryUrls"] = list(dict.fromkeys(u for u in urls if github_repository_url(u)))
        data["sourceUrls"] = list(dict.fromkeys(u for u in urls if web_url(u) and not github_repository_url(u)))
        converted_links |= bool(data["sourceUrls"])
        data["authors"] = [a.name for a in record.authors if a.name.strip()]
        # Preserve identity metadata separately, not as the shared authors field.
        data["authorRecords"] = [a.model_dump() for a in record.authors]
        try:
            ref = paper_ref(data)
            if not record.title or not record.title.strip():
                raise ValueError("missing title")
            start, end = payload["constraints"].get("yearFrom"), payload["constraints"].get("yearTo")
            if start is not None or end is not None:
                if record.year is None or (start is not None and record.year < start) or (end is not None and record.year > end):
                    raise ValueError("year outside requested range")
            key = ref["doi"] or ref["url"] or ref["pdfUrl"]
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
        except ValueError:
            dropped += 1
            continue
        data["provider"] = response.provider
        data["relevance"] = "关键词检索返回；未经全文阅读或相关性人工复核。"
        result["items"].append({"kind": "paper", "data": data})
    if not result["items"]:
        raise AdapterError("NO_VALID_PAPER_RECORDS")
    if len(result["items"]) > request["limit"]:
        result["warnings"].append("TRUNCATED：已按用户指定的数量上限截取。")
    result["items"] = result["items"][:request["limit"]]
    if dropped:
        result["warnings"].append(f"FILTERED_RECORDS：去除 {dropped} 条重复、缺少依据或年份不符合的记录。")
    if converted_links:
        result["warnings"].append("SOURCE_URLS_NORMALIZED：论文地址保存为 sourceUrls，不作为 GitHub 仓库地址。")
    result["summary"] = f"保留 {len(result['items'])} 篇可定位论文；未阅读全文，未分析代码。"
    diagnostics.update(outcome="papers", returnedCount=len(result["items"]), droppedCount=dropped)
    validate_result(result, payload)
    return {"result": result, "error": None, "diagnostics": diagnostics}


async def call_wrapper(payload, request_id, agent, request_model, *, timeout_seconds=45):
    request = to_wrapper_request(payload, request_id)
    try:
        if timeout_seconds <= 0:
            raise AdapterError("INVALID_TIMEOUT")
        response = await asyncio.wait_for(
            agent.literature_search(request_model.model_validate(request)), timeout_seconds)
        return normalize_response(payload, request, response.model_dump(mode="json"))
    except AdapterError:
        raise
    except asyncio.TimeoutError:
        raise AdapterError("WRAPPER_TIMEOUT") from None
    except Exception:
        raise AdapterError("WRAPPER_CALL_FAILED") from None


def load_wrapper(root):
    """Import only the explicit team delivery path, under an isolated package name."""
    root = Path(root).resolve()
    package = root / "researchmesh_literature"
    if not (package / "__init__.py").is_file():
        raise AdapterError("WRAPPER_ROOT_NOT_FOUND")
    name = "_researchmesh_zhang_literature"
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(existing.__file__).resolve() != package / "__init__.py":
            raise AdapterError("WRAPPER_ALREADY_LOADED_FROM_OTHER_ROOT")
        return existing
    spec = importlib.util.spec_from_file_location(name, package / "__init__.py",
                                                 submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
        raise AdapterError("WRAPPER_IMPORT_FAILED_CHECK_DEPENDENCIES") from None
    return module


def verify_replay_request(request, fixture_request):
    """Replay cannot claim that one historical query answers a different plan."""
    filters = fixture_request.get("filters", {})
    defaults = {"year": None, "publicationDateOrYear": None, "publicationTypes": [],
                "venues": [], "fieldsOfStudy": [], "openAccessOnly": False, "minCitationCount": None}
    expected = {**defaults, **request["filters"]}
    observed = {**defaults, **filters}
    if (fixture_request.get("schemaVersion") != request["schemaVersion"]
            or fixture_request.get("query") != request["query"]
            or fixture_request.get("limit") != request["limit"]
            or fixture_request.get("requestId") != request["requestId"] or observed != expected):
        raise AdapterError("REPLAY_QUERY_OR_FILTER_MISMATCH")
