"""自然语言 -> 真实 Planner -> 单独文献检索；直接本地调用，不是 AIP 链路。"""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time

from deepseek_model import safe_failure
from input_binding import paper_ref
from literature_adapter import AdapterError, to_wrapper_request
from llm_provider import ProviderSettings
from run_literature import (execute_live_task, prepare_live_wrapper,
                            render_literature_report, select_task)
from run_planner import create_plan

ROOT = Path(__file__).resolve().parent


async def run_pipeline(query, *, planner_call, search_call, saved_plan=None, on_plan=None):
    """依赖注入仅供离线测试；正式入口只使用真实模块，无 Mock 兜底。"""
    start = time.perf_counter()
    record = {
        "plan": None, "plannerMetadata": None, "outcome": None,
        "metadata": {
            "mode": "live-planner-direct-literature", "fixtureReplay": False,
            "llmUsed": False, "agentsExecuted": False, "realSourceRequested": False,
            "aipUsed": False, "platformDiscoveryUsed": False,
            "paperReadingExecuted": False, "repositoryAnalysisExecuted": False,
            "pipelineOutcome": "not-started",
        },
        "error": None,
    }
    meta = record["metadata"]
    try:
        if not isinstance(query, str) or not query.strip() or len(query) > 6000:
            raise AdapterError("INVALID_QUERY")
        if saved_plan is None:
            meta["pipelineOutcome"] = "planning"
            # 标记开始了调用流程；真正请求次数以模型返回的 metadata 为准。
            meta["llmUsed"] = True
            plan, planner_meta = await planner_call(query)
            record["plannerMetadata"] = planner_meta
        else:
            plan = deepcopy(saved_plan)
            meta.update(mode="resume-direct-literature", resumedPlan=True)
        record["plan"] = plan
        if plan.get("userQuery") != query:
            raise AdapterError("PLAN_QUERY_MISMATCH")
        # 澄清也要验证完整计划，不能携带待执行任务。
        from planner_contract import validate_plan
        validate_plan(plan)
        if plan["needsClarification"]:
            meta["pipelineOutcome"] = "needs-clarification"
            return record
        payload = select_task(plan)
        to_wrapper_request(payload, "preflight")
        meta["pipelineOutcome"] = "planned"
        if on_plan:
            on_plan(record)
        meta.update(pipelineOutcome="retrieving", agentsExecuted=True,
                    realSourceRequested=True)
        record["outcome"] = await search_call(payload)
        if record["outcome"]["error"]:
            record["error"] = record["outcome"]["error"]
            meta["pipelineOutcome"] = "retrieval-failed"
        elif not record["outcome"]["result"]["items"]:
            meta["pipelineOutcome"] = "empty"
        else:
            meta["pipelineOutcome"] = "completed"
    except AdapterError as exc:
        record["error"] = {"code": exc.code}
        meta["pipelineOutcome"] = "not-executed" if not meta["agentsExecuted"] else "retrieval-failed"
    except Exception as exc:
        # 原始 SDK 异常可能含认证数据，绝不存入记录或打印。
        record["error"] = {"code": "PIPELINE_FAILED", "message": safe_failure(exc)}
        meta["pipelineOutcome"] = "failed"
    finally:
        meta.update(elapsedMs=round((time.perf_counter() - start) * 1000),
                    verifiedAt=datetime.now().astimezone().isoformat())
    return record


def render_pipeline_report(record):
    if record["outcome"] is not None:
        return render_literature_report(record["plan"], record["outcome"], "live",
                                       llm_used=record["metadata"]["llmUsed"])
    lines = ["# ResearchMesh 自然语言规划与检索报告", "",
             "> 本入口仅支持单独文献检索；未调用 ADP 或 AIP，不是平台互联验收。", ""]
    if record["plan"]:
        lines += ["研究需求：" + record["plan"]["userQuery"], ""]
    if record["metadata"]["pipelineOutcome"] == "needs-clarification":
        lines += ["需要补充信息：" + record["plan"]["clarificationQuestion"],
                  "未执行论文检索；请补全研究问题后重新运行。"]
    elif record["error"]:
        lines += ["未完成：" + record["error"]["code"],
                  "请查看 plan.json 和 metadata.json；没有使用模拟数据或历史结果兜底。"]
    else:
        lines += ["任务计划已保存；检索尚未完成。可以使用 --resume 续跑，不重新生成计划。"]
    return "\n".join(lines) + "\n"


def save_pipeline(record, output_root=None, *, output_dir=None):
    if output_dir is None:
        output = Path(output_root if output_root is not None else ROOT / "outputs") / (
            datetime.now().strftime("research-live-%Y%m%d-%H%M%S-%f"))
        output.mkdir(parents=True, exist_ok=False)
    else:
        # 仅更新本次运行自己创建的检查点；续跑始终另建目录，不覆盖历史记录。
        output = Path(output_dir)
    records = {"metadata": record["metadata"], "pipeline": record}
    for key, filename in (("plan", "plan"), ("plannerMetadata", "planner_metadata"),
                          ("outcome", "outcome")):
        if record[key] is not None:
            records[filename] = record[key]
    outcome = record["outcome"]
    if outcome is not None and outcome["result"] is not None:
        records["reader_inputs"] = {
            "papers": [paper_ref(item["data"]) for item in outcome["result"]["items"]],
            "note": "只生成可交接输入，未调用 Paper Reader。",
        }
    for name, data in records.items():
        (output / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text(render_pipeline_report(record), encoding="utf-8")
    return output


def load_resume_plan(path):
    path = Path(path)
    source = path / "plan.json" if path.is_dir() else path
    try:
        plan = json.loads(source.read_text(encoding="utf-8"))
        payload = select_task(plan)
        to_wrapper_request(payload, "resume-preflight")
    except (OSError, ValueError, TypeError, KeyError):
        raise AdapterError("INVALID_RESUME_PLAN") from None
    return plan


def print_resume_command(output):
    print("计划已保存；无需重新输入问题或调用 DeepSeek，续跑命令：")
    print(f'.\\.venv\\Scripts\\python.exe -X utf8 run_research.py --resume "{output}"')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--query", help="省略时在终端用自然语言输入，不用 JSON")
    mode.add_argument("--resume", type=Path, help="续跑保存的目录或 plan.json；不输入问题、不调用模型")
    parser.add_argument("--wrapper-root", type=Path, default=ROOT / "agents" / "literature")
    args = parser.parse_args(argv)
    print("【仅续跑检索】不调用模型、不重复输入问题；不使用 ADP 或 AIP。" if args.resume else
          "【真实规划 + 真实检索】模型最多调用 2 次，会消耗额度；不使用 ADP 或 AIP。")
    output = None
    try:
        saved_plan = load_resume_plan(args.resume) if args.resume else None
        query = saved_plan["userQuery"] if saved_plan is not None else (
            args.query if args.query is not None else input("请输入研究问题：")).strip()
        if not query or len(query) > 6000:
            print("请输入 1—6000 字的研究问题；未调用接口。")
            return 1
        # 两个 Key / 本机模块先预检，避免规划计费后才发现无法检索。
        if saved_plan is None:
            ProviderSettings.from_env()
        prepared = prepare_live_wrapper(args.wrapper_root)
        print("已读取并校验保存的计划；跳过模型。" if saved_plan is not None else
              "1/2 正在生成并校验任务计划……")

        def checkpoint(record):
            nonlocal output
            if args.resume:
                record["metadata"]["resumeFrom"] = str(args.resume.resolve())
            output = save_pipeline(record)
            print("计划已保存：", output)

        async def search(payload):
            print("2/2 正在调用论文检索 Agent……")
            return await execute_live_task(payload, args.wrapper_root, prepared=prepared,
                                           request_source="run_research")

        record = asyncio.run(run_pipeline(query, planner_call=create_plan, search_call=search,
                                         saved_plan=saved_plan, on_plan=checkpoint))
        output = save_pipeline(record, output_dir=output)
        state = record["metadata"]["pipelineOutcome"]
        if state == "needs-clarification":
            print("需要补充信息：", record["plan"]["clarificationQuestion"])
            print("本次未执行检索。补全问题后重新运行即可。")
        elif record["error"]:
            print("未完成：", record["error"]["code"])
            if record["error"]["code"] == "ONLY_STANDALONE_LITERATURE_PLAN_SUPPORTED":
                print("当前入口只支持单独检索。请明确“不阅读全文、不分析代码”，或使用其他已有入口。")
            elif record["error"].get("message"):
                print(record["error"]["message"])
            if record["metadata"]["agentsExecuted"] and record["plan"]:
                print_resume_command(output)
        else:
            print("结果：", record["outcome"]["result"]["summary"])
        print("记录：", output)
        return 1 if record["error"] else 0
    except (KeyboardInterrupt, EOFError):
        print("已取消；停止后续请求。")
        if output is not None:
            print_resume_command(output)
    except AdapterError as exc:
        print("预检未通过：", exc.code, "；未调用模型或检索 API。")
    except Exception as exc:
        print(safe_failure(exc))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
