"""显式启用的本地 Literature → Reader 流程；不发现平台、不构造 AIP 消息。"""
import asyncio
from copy import deepcopy
from datetime import datetime
import time

from deepseek_model import safe_failure
from input_binding import bind_task_input, InputBindingError
from literature_adapter import AdapterError, to_wrapper_request
from orchestrator import make_task_payload
from planner_contract import validate_plan, UnsupportedConstraintError
from reader_adapter import ReaderAdapterError
from reader_batch_adapter import prepare_reader_batch, validate_reader_checkpoint
from result_contract import validate_result
from arxiv_pdf import validate_pdf_enrichment


def select_reader_chain(plan):
    validate_plan(plan)
    tasks = plan["tasks"]
    if (plan["needsClarification"] or len(tasks) != 2
            or tasks[0]["taskType"] != "literature_search" or tasks[0]["dependsOn"]
            or tasks[1]["taskType"] != "paper_read"
            or tasks[1]["dependsOn"] != [tasks[0]["subtaskId"]]):
        raise AdapterError("ONLY_LITERATURE_READER_CHAIN_SUPPORTED")
    search, reader = deepcopy(tasks)
    to_wrapper_request(make_task_payload(search), "preflight")
    # 无上游结果时也先检查全部 Reader 配置，避免检索后才发现不支持。
    if set(reader["constraints"]) - {"maxPapers"}:
        raise ReaderAdapterError("UNSUPPORTED_CONSTRAINT")
    return search, reader


def _search_result(outcome, task):
    if not isinstance(outcome, dict) or outcome.get("error") is not None or not outcome.get("result"):
        raise AdapterError("INVALID_SEARCH_CHECKPOINT")
    try:
        validate_result(outcome["result"], task)
        if any(item["data"].get("isMock") is True for item in outcome["result"]["items"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise AdapterError("INVALID_SEARCH_CHECKPOINT") from None
    return deepcopy(outcome["result"])


async def run_reader_pipeline(query, *, planner_call, search_call, reader_call,
                              saved_plan=None, saved_record=None, on_plan=None,
                              on_progress=None, pdf_resolver=None, prepare_sources_only=False):
    """reader_call 使用批次适配器的 saved_outcomes/on_paper/on_start 参数。"""
    start = time.perf_counter()
    record = {"plan": None, "plannerMetadata": None, "outcome": None,
              "readerTask": None, "readerProgress": [], "readerOutcome": None, "error": None,
              "metadata": {"mode": "live-planner-direct-literature-reader", "fixtureReplay": False,
                           "llmUsed": False, "agentsExecuted": False, "realSourceRequested": False,
                           "aipUsed": False, "platformDiscoveryUsed": False,
                           "paperReadingExecuted": False, "repositoryAnalysisExecuted": False,
                           "literatureReused": False, "pipelineOutcome": "not-started"}}
    meta = record["metadata"]
    def checkpoint():
        if on_progress is not None:
            on_progress(record)
    try:
        if not isinstance(query, str) or not query.strip() or len(query) > 6000:
            raise AdapterError("INVALID_QUERY")
        if prepare_sources_only and (saved_plan is None or saved_record is None
                or not saved_record.get("outcome") or saved_record["outcome"].get("error") is not None):
            raise AdapterError("PDF_PREPARATION_REQUIRES_SUCCESSFUL_SEARCH")
        if saved_record is not None and saved_plan is None:
            raise AdapterError("INVALID_PIPELINE_CHECKPOINT")
        if saved_plan is None:
            meta.update(pipelineOutcome="planning", llmUsed=True)
            plan, record["plannerMetadata"] = await planner_call(query)
        else:
            plan = deepcopy(saved_plan)
            meta.update(mode="resume-direct-literature-reader", resumedPlan=True)
        record["plan"] = plan
        if plan.get("userQuery") != query:
            raise AdapterError("PLAN_QUERY_MISMATCH")
        validate_plan(plan)
        if plan["needsClarification"]:
            meta["pipelineOutcome"] = "needs-clarification"
            return record
        search_task, reader_task = select_reader_chain(plan)
        saved = deepcopy(saved_record)
        if saved is not None and saved.get("plan") != plan:
            raise AdapterError("INVALID_PIPELINE_CHECKPOINT")
        cached_search = saved.get("outcome") if saved else None
        if cached_search is not None and cached_search.get("error") is None:
            _search_result(cached_search, search_task)
            record["outcome"] = cached_search
            meta["literatureReused"] = True
        meta["pipelineOutcome"] = "planned"
        if on_plan is not None:
            on_plan(record)
        if not meta["literatureReused"]:
            meta.update(pipelineOutcome="retrieving", agentsExecuted=True, realSourceRequested=True)
            record["outcome"] = await search_call(make_task_payload(search_task))
        if record["outcome"]["error"]:
            record["error"] = record["outcome"]["error"]
            meta["pipelineOutcome"] = "retrieval-failed"
            checkpoint()
            return record
        result = _search_result(record["outcome"], search_task)
        if not result["items"]:
            meta["pipelineOutcome"] = "empty"
            checkpoint()
            return record
        bound = bind_task_input(reader_task, {search_task["subtaskId"]: result})
        parent, children = prepare_reader_batch(bound)
        before_resolution = deepcopy(parent)
        # 在任何来源请求之前校验旧笔记，并保留已成功部分，避免解析中断丢失进度。
        if saved is not None and saved.get("readerTask") is not None:
            old_parent, old_children = prepare_reader_batch(saved["readerTask"])
            if old_parent != parent:
                if pdf_resolver is None:
                    raise AdapterError("INVALID_READER_CHECKPOINT")
                try:
                    validate_pdf_enrichment(parent, old_parent)
                except ReaderAdapterError:
                    raise AdapterError("INVALID_READER_CHECKPOINT") from None
            old_cached = validate_reader_checkpoint(old_children, saved.get("readerProgress", []))
            record["readerTask"] = deepcopy(old_parent)
            record["readerProgress"] = [deepcopy(p) for p in saved.get("readerProgress", [])
                                        if p["subtaskId"] in old_cached]
        if pdf_resolver is not None:
            record["readerTaskBeforePdfResolution"] = before_resolution
            record["pdfResolution"] = deepcopy(saved.get("pdfResolution", [])) if saved else []
            meta.update(pipelineOutcome="resolving-pdf", pdfSourceResolutionEnabled=True,
                        retrievedCount=len(result["items"]), selectedPaperCount=len(parent["input"]["papers"]))
            checkpoint()
            resolved, events = await pdf_resolver(parent, result, previous=record["pdfResolution"])
            validate_pdf_enrichment(parent, resolved)
            parent, children = prepare_reader_batch(resolved)
            record["pdfResolution"] = events
            meta["pdfResolutionNetworkUsed"] = any(e.get("networkAttempted") is True for e in events)
        record["readerTask"] = parent
        meta.update(retrievedCount=len(result["items"]), selectedPaperCount=len(parent["input"]["papers"]),
                    selectionPolicy="explicit-input-first-otherwise-upstream-order-maxPapers")
        cached_papers = None
        if saved is not None and saved.get("readerTask") is not None:
            if saved["readerTask"] not in (parent, before_resolution):
                raise AdapterError("INVALID_READER_CHECKPOINT")
            cached_papers = saved.get("readerProgress", [])
        cached = validate_reader_checkpoint(children, cached_papers)
        record["readerProgress"] = []
        # 保留全部已校验成功项，避免续跑在中间再次中断时丢掉后面的成功论文。
        for child in children:
            if child["subtaskId"] in cached:
                record["readerProgress"].append({"paperId": child["input"]["papers"][0]["paperId"],
                    "subtaskId": child["subtaskId"], "succeeded": True, "reused": True,
                    **deepcopy(cached[child["subtaskId"]])})
        meta["pipelineOutcome"] = "reader-ready"
        checkpoint()  # 检索结果及绑定输入必须在第一次阅读请求前落盘。
        if prepare_sources_only:
            meta.update(pdfSourcePreparationOnly=True, llmUsed=False)
            if any(p["pdfUrl"] is None for p in parent["input"]["papers"]):
                meta["pipelineOutcome"] = "pdf-unavailable"
                record["error"] = {"code": "PDF_SOURCE_RESOLUTION_INCOMPLETE"}
            checkpoint()
            return record
        meta["pipelineOutcome"] = "reading"
        def on_start(child):
            meta.update(paperReadingExecuted=True, agentsExecuted=True)
            if child["input"]["papers"][0]["pdfUrl"] is not None:
                # 表示进入可能计费的阅读流程，请求次数以 Provider 记录为准。
                meta["llmUsed"] = True
            checkpoint()
        def on_paper(paper):
            progress = {item["subtaskId"]: item for item in record["readerProgress"]}
            progress[paper["subtaskId"]] = deepcopy(paper)
            record["readerProgress"] = [progress[child["subtaskId"]] for child in children
                                        if child["subtaskId"] in progress]
            checkpoint()
        record["readerOutcome"] = await reader_call(parent, saved_outcomes=cached_papers,
                                                    on_paper=on_paper, on_start=on_start)
        outcome = record["readerOutcome"]
        validate_result(outcome["result"], parent)
        record["error"] = outcome["error"]
        meta["pipelineOutcome"] = ("completed" if outcome["error"] is None
                                    else "partial" if outcome["result"]["items"] else "reading-failed")
        checkpoint()
    except asyncio.CancelledError:
        meta["pipelineOutcome"] = "interrupted"
        checkpoint()
        raise
    except (AdapterError, ReaderAdapterError, UnsupportedConstraintError) as exc:
        record["error"] = {"code": exc.code}
        meta["pipelineOutcome"] = "not-executed" if not meta["agentsExecuted"] else "failed"
    except InputBindingError:
        record["error"] = {"code": "INPUT_INSUFFICIENT"}
        meta["pipelineOutcome"] = "failed"
    except Exception as exc:
        record["error"] = {"code": "PIPELINE_FAILED", "message": safe_failure(exc)}
        meta["pipelineOutcome"] = "failed"
    finally:
        meta.update(elapsedMs=round((time.perf_counter() - start) * 1000),
                    verifiedAt=datetime.now().astimezone().isoformat())
    return record


def render_reader_report(record):
    meta = record["metadata"]
    lines = ["# ResearchMesh 检索与全文阅读报告", "",
             "> 本地直接调用；未调用 ADP 或 AIP，不是平台互联验收。", "",
             "状态：" + meta["pipelineOutcome"], ""]
    if meta.get("syntheticData") or meta.get("fixtureReplay"):
        lines += ["> 离线合成夹具验收；未调用真实模型、未读取真实 PDF，内容不作为科研结论。", ""]
    if record["plan"]:
        lines += ["研究需求：" + record["plan"]["userQuery"], ""]
        if record["plan"]["needsClarification"]:
            lines += ["需要补充信息：" + record["plan"]["clarificationQuestion"], ""]
    if meta.get("literatureReused"):
        lines += ["检索结果来自显式指定的检查点，本次未重复请求检索接口。", ""]
    if meta.get("pdfSourcePreparationOnly"):
        lines += ["本次仅核验/补全已知来源的 PDF；未规划、未检索、未调用 Reader 或模型。", ""]
    for event in record.get("pdfResolution", []):
        if event["state"] == "verified":
            lines += ["已核验全文：" + event["pdfUrl"], "来源页面：" + event["sourceUrl"],
                      "缓存：" + ("已复用并校验哈希" if event.get("cacheReused") else "已保存核验过的 PDF"), ""]
        else:
            lines += ["全文来源未核验：" + event["paperId"] + " / " + event["reason"], ""]
    if record["readerTask"]:
        lines += [f"检索返回 {meta['retrievedCount']} 篇；按显式输入优先、否则上游顺序及 maxPapers 选择 "
                  f"{meta['selectedPaperCount']} 篇，不代表智能排序。", ""]
    reader = record["readerOutcome"]
    if reader is not None:
        lines += [reader["result"]["summary"], ""]
    # 中断时也展示已经校验并落盘的成功笔记。
    for paper in record["readerProgress"]:
        if paper.get("result") and paper["result"]["warnings"]:
            lines += ["阅读警告：" + "；".join(paper["result"]["warnings"]), ""]
        if not paper["succeeded"]:
            lines += [f"阅读失败：{paper['paperId']} / {paper['error']['code']}", ""]
            continue
        for item in paper["result"]["items"]:
            note = item["data"]
            lines += ["## " + (note["source"]["title"] or paper["paperId"]), "",
                      "来源：" + (note["source"]["pdfUrl"] or ""), "",
                      "方法：" + note["method"], "", "发现：" + note["findings"], "",
                      "限制：" + note["limitations"], ""]
            for evidence in note["evidence"]:
                lines += [f"- {evidence['locator']}：{evidence['supports']}"]
            lines.append("")
    if record["error"]:
        lines += ["未完成：" + record["error"]["code"], ""]
    if reader is not None and reader["error"]:
        lines += ["成功笔记已保留；部分失败不算整批完成。", ""]
    if record["readerTask"] is None:
        lines += ["尚未执行全文阅读。", ""]
    elif meta["pipelineOutcome"] in ("reading", "reader-ready", "interrupted"):
        lines += [f"已保存 {sum(p['succeeded'] for p in record['readerProgress'])}/{meta['selectedPaperCount']} 篇成功笔记；整批尚未完成。", ""]
    lines += ["显式 --resume 复用已校验的检索与成功笔记；失败或未完成的论文会再次尝试，可能产生模型费用。", ""]
    return "\n".join(lines)
