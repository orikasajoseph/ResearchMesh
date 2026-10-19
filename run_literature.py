"""显式选择队友样例回放或真实 Wrapper 调用；不调用 LLM、ADP 或 AIP。"""
import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from input_binding import paper_ref
from literature_adapter import (AdapterError, call_wrapper, load_wrapper,
                                normalize_response, to_wrapper_request, verify_replay_request)
from orchestrator import make_task_payload
from planner_contract import validate_plan
from search_recovery import recover_rate_limit

ROOT = Path(__file__).resolve().parent


def select_task(plan):
    validate_plan(plan)
    if plan["needsClarification"]:
        raise AdapterError("PLAN_NEEDS_CLARIFICATION")
    if len(plan["tasks"]) != 1 or plan["tasks"][0]["taskType"] != "literature_search" or plan["tasks"][0]["dependsOn"]:
        raise AdapterError("ONLY_STANDALONE_LITERATURE_PLAN_SUPPORTED")
    return make_task_payload(plan["tasks"][0])


def render_literature_report(plan, outcome, mode, *, llm_used=False):
    lines = ["# ResearchMesh 文献检索接入报告", "",
             ("> 队友历史样例回放：本次没有实时请求 Semantic Scholar。" if mode == "replay"
              else "> 本次调用真实 Literature Wrapper；未执行论文阅读或代码分析。"),
             ("> 本次使用真实 LLM 规划并直接调用 Literature Wrapper；未调用 ADP 或 AIP，不能作为平台互联验收。"
              if llm_used else "> 本次未调用 LLM、ADP 或 AIP，不能作为平台互联验收。"), "",
             "研究需求：" + plan["userQuery"], ""]
    if outcome["error"]:
        lines += ["执行未成功：" + outcome["error"]["code"]]
    else:
        result = outcome["result"]
        lines += [result["summary"], ""]
        for index, item in enumerate(result["items"], 1):
            paper = item["data"]
            lines += [f"## {index} {paper['title']}", "",
                      "作者：" + ("、".join(paper["authors"]) or "未提供"),
                      f"年份：{paper.get('year') or '未提供'}",
                      f"DOI：{paper.get('doi') or '未提供'}",
                      f"论文链接：{paper.get('url') or '未提供'}",
                      f"开放 PDF：{paper.get('pdfUrl') or '未提供，未构造或下载'}",
                      "其他论文来源：" + ("；".join(paper["sourceUrls"]) or "未提供"),
                      "代码仓库：" + ("；".join(paper["repositoryUrls"]) or "未确认"), ""]
        lines += ["## 注意事项", "", *result["warnings"], "",
                  "以上为检索元数据，不是论文质量评价、全文分析或复现结论。"]
    return "\n".join(lines) + "\n"


def prepare_live_wrapper(wrapper_root):
    """预检本机 Key 和 Wrapper；不联网，不回显凭据。"""
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY",
                         values.get("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
    search_provider = os.getenv("LITERATURE_SEARCH_PROVIDER", "semantic-scholar").strip().lower()
    if search_provider not in {"semantic-scholar", "openalex"}:
        raise AdapterError("INVALID_LITERATURE_SEARCH_PROVIDER")
    if not key and search_provider == "semantic-scholar":
        raise AdapterError("MISSING_SEMANTIC_SCHOLAR_API_KEY")
    wrapper = load_wrapper(wrapper_root)
    resolve_resources = os.getenv("LITERATURE_RESOLVE_RESOURCES", "1").strip().lower() in {"1", "true", "yes"}
    trust_env_setting = os.getenv("LITERATURE_OPENALEX_TRUST_ENV",
                                   values.get("LITERATURE_OPENALEX_TRUST_ENV") or "1").strip().lower()
    if trust_env_setting not in {"1", "true", "yes", "0", "false", "no"}:
        raise AdapterError("INVALID_LITERATURE_OPENALEX_TRUST_ENV")
    resource_trust_env_setting = os.getenv("LITERATURE_RESOURCE_TRUST_ENV",
                                            values.get("LITERATURE_RESOURCE_TRUST_ENV") or "1").strip().lower()
    if resource_trust_env_setting not in {"1", "true", "yes", "0", "false", "no"}:
        raise AdapterError("INVALID_LITERATURE_RESOURCE_TRUST_ENV")
    config = wrapper.LiteratureAgentConfig(api_key=key, timeout_seconds=20, max_retries=0,
                                           resolve_resources=resolve_resources,
                                           search_provider=search_provider,
                                           openalex_fallback=os.getenv("LITERATURE_OPENALEX_FALLBACK", "1").lower() in {"1", "true", "yes"},
                                           openalex_api_key=os.getenv("OPENALEX_API_KEY", values.get("OPENALEX_API_KEY") or "") or None,
                                           openalex_trust_env=trust_env_setting in {"1", "true", "yes"},
                                           resource_trust_env=resource_trust_env_setting in {"1", "true", "yes"})
    return wrapper, config


async def execute_live_task(payload, wrapper_root, *, prepared=None, progress=print,
                            request_source="run_literature", retry=True):
    """供独立检索和自然语言入口共用；不调用模型、发现或 AIP。"""
    # 在创建 Provider 或联网之前检查所有业务限制。
    request_id = "literature-" + uuid4().hex
    to_wrapper_request(payload, request_id)
    wrapper, config = prepared if prepared is not None else prepare_live_wrapper(wrapper_root)
    provider = wrapper.SemanticScholarProvider(config=config, request_source=request_source,
                                               subtask_id=payload["subtaskId"])
    agent = wrapper.LiteratureAgent(provider)

    async def attempt():
        # Search and resource lookup share this deadline. Keep it below the live
        # Partner's 120s budget so a slow resource lookup returns diagnostics.
        resource_budget = (getattr(config, "resource_max_requests", 12)
                           * getattr(config, "resource_timeout_seconds", 5)) if getattr(config, "resolve_resources", False) else 0
        search_budget = getattr(config, "timeout_seconds", 20)
        fallback_budget = getattr(config, "openalex_timeout_seconds", 8) if getattr(config, "openalex_fallback", False) else 0
        wrapper_budget = min(110, max(45, search_budget + fallback_budget + resource_budget + 5))
        result = await call_wrapper(payload, request_id, agent, wrapper.LiteratureSearchRequest,
                                    timeout_seconds=wrapper_budget)
        if callable(getattr(provider, "http_diagnostics", None)):
            diagnostics = provider.http_diagnostics()
            if isinstance(diagnostics, dict):
                result["diagnostics"].update(diagnostics)
        if callable(getattr(provider, "resource_diagnostics", None)):
            diagnostics = provider.resource_diagnostics()
            if isinstance(diagnostics, dict):
                result["diagnostics"].update(diagnostics)
        return result

    # 同一个 Agent / Provider / requestId，保留限速器；SDK 和 Provider 配置均无内层重试。
    fallback_enabled = bool(getattr(config, "openalex_fallback", False)) or getattr(config, "search_provider", None) == "openalex"
    return await recover_rate_limit(attempt, progress=progress,
                                    delays=() if not retry or fallback_enabled else (2, 4, 8))


async def execute(args):
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    payload = select_task(plan)
    if args.mode == "replay":
        if not args.fixture_input or not args.fixture_output:
            raise AdapterError("REPLAY_REQUIRES_INPUT_AND_OUTPUT")
        fixture = json.loads(Path(args.fixture_input).read_text(encoding="utf-8"))
        raw = json.loads(Path(args.fixture_output).read_text(encoding="utf-8"))
        request = to_wrapper_request(payload, fixture.get("requestId"))
        verify_replay_request(request, fixture)
        outcome = normalize_response(payload, request, raw)
    else:
        if not args.wrapper_root:
            raise AdapterError("LIVE_REQUIRES_WRAPPER_ROOT")
        outcome = await execute_live_task(payload, args.wrapper_root)
    metadata = {"mode": args.mode, "fixtureReplay": args.mode == "replay",
                "realSourceRequested": args.mode == "live", "llmUsed": False,
                "aipUsed": False, "platformDiscoveryUsed": False,
                "paperReadingExecuted": False, "repositoryAnalysisExecuted": False,
                "verifiedAt": datetime.now().astimezone().isoformat()}
    output = ROOT / "outputs" / datetime.now().strftime(f"literature-{args.mode}-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    records = {"plan": plan, "outcome": outcome, "metadata": metadata}
    if outcome["result"]:
        records["reader_inputs"] = {"papers": [paper_ref(i["data"]) for i in outcome["result"]["items"]],
                                    "note": "只生成可交接输入，未调用 Paper Reader。"}
    for name, data in records.items():
        (output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text(render_literature_report(plan, outcome, args.mode), encoding="utf-8")
    print("模式：", args.mode, "；未调用 LLM、ADP 或 AIP。")
    print("结果：", outcome["error"]["code"] if outcome["error"] else outcome["result"]["summary"])
    print("记录：", output)
    return 1 if outcome["error"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["replay", "live"], required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--wrapper-root")
    parser.add_argument("--fixture-input")
    parser.add_argument("--fixture-output")
    args = parser.parse_args()
    try:
        return asyncio.run(execute(args))
    except AdapterError as exc:
        print("未完成：", exc.code)
    except (KeyboardInterrupt, EOFError):
        print("已取消")
    except Exception:
        print("未完成：INPUT_OR_RUNTIME_ERROR；请核对文件和依赖，不回显可能含凭据的异常。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
