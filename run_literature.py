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

ROOT = Path(__file__).resolve().parent


def select_task(plan):
    validate_plan(plan)
    if plan["needsClarification"]:
        raise AdapterError("PLAN_NEEDS_CLARIFICATION")
    if len(plan["tasks"]) != 1 or plan["tasks"][0]["taskType"] != "literature_search" or plan["tasks"][0]["dependsOn"]:
        raise AdapterError("ONLY_STANDALONE_LITERATURE_PLAN_SUPPORTED")
    return make_task_payload(plan["tasks"][0])


def render_literature_report(plan, outcome, mode):
    lines = ["# ResearchMesh 文献检索接入报告", "",
             ("> 队友历史样例回放：本次没有实时请求 Semantic Scholar。" if mode == "replay"
              else "> 本次调用真实 Literature Wrapper；未执行论文阅读或代码分析。"),
             "> 本次未调用 LLM、ADP 或 AIP，不能作为平台互联验收。", "",
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
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        if not os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip():
            raise AdapterError("MISSING_SEMANTIC_SCHOLAR_API_KEY")
        wrapper = load_wrapper(args.wrapper_root)
        # Explicit bounded config: don't autoload a second team's .env, don't layer retries.
        config = wrapper.LiteratureAgentConfig(api_key=os.environ["SEMANTIC_SCHOLAR_API_KEY"],
                                                timeout_seconds=20, max_retries=0)
        provider = wrapper.SemanticScholarProvider(config=config)
        outcome = await call_wrapper(payload, "literature-" + uuid4().hex,
                                     wrapper.LiteratureAgent(provider), wrapper.LiteratureSearchRequest)
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
