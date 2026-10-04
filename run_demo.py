"""一条命令运行模拟闭环；不会联网，不会改变团队共享文档。"""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path

from aggregator import render_report
from demo_planner import ExampleModel
from mock_agents import build_mock_agents
from orchestrator import LocalOrchestrator
from planner import Planner
from resolver import MockAgentResolver


async def run_example(case_number, fail_task=None):
    root = Path(__file__).resolve().parent
    cases = json.loads((root / "examples" / "planner_cases.json").read_text(encoding="utf-8"))
    if not 1 <= case_number <= len(cases):
        raise ValueError(f"case 必须在 1 到 {len(cases)} 之间")
    example = cases[case_number - 1]["plan"]
    plan = await Planner(ExampleModel(example)).plan(example["userQuery"])
    if fail_task and fail_task not in {task["subtaskId"] for task in plan["tasks"]}:
        raise ValueError("fail-task 必须是当前样例中的任务编号")
    resolver = MockAgentResolver(build_mock_agents(fail_task))
    run = await LocalOrchestrator(resolver).run_plan(plan)
    run["plannerMode"] = "fixture"
    return plan, run


def main():
    parser = argparse.ArgumentParser(description="ResearchMesh 本地模拟闭环；不是 AIP 互联或真实科研结果")
    parser.add_argument("--case", type=int, default=1, help="预写样例编号：1完整调研，2指定论文，3模糊需求")
    parser.add_argument("--fail-task", help="模拟任务失败，例如 S2")
    args = parser.parse_args()
    try:
        plan, run = asyncio.run(run_example(args.case, args.fail_task))
    except ValueError as exc:
        parser.error(str(exc))

    # 每次创建新目录，不覆盖先前的演示结果。
    root = Path(__file__).resolve().parent
    output = root / "outputs" / datetime.now().strftime("mock-run-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    for name, data in (("plan.json", plan), ("research_result.json", run),
                       ("run_log.json", run["executionRecords"])):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.md").write_text(render_report(run), encoding="utf-8")
    print("【仅模拟】固定样例规划 + 本地能力解析 + 模拟执行；无真实模型或 AIP。")
    print(f"成功任务：{len(run['results'])}；需要澄清：{run['needsClarification']}")
    for record in run["executionRecords"]:
        outcome = "成功" if record["succeeded"] else ("跳过" if record["skipped"] else "未成功")
        print(f"{record['subtaskId']} -> {record['agentId'] or '无执行者'}：{outcome}")
    print(f"报告和 JSON 已保存：{output}")


if __name__ == "__main__":
    main()
