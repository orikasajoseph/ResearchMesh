"""真实 Planner 独立入口；不会执行论文工具、Mock 执行者或 AIP。"""

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import time


async def create_plan(query, attempts):
    from deepseek_model import DeepSeekModel, DeepSeekSettings
    from planner import Planner
    settings = DeepSeekSettings.from_env()
    start = time.perf_counter()
    async with DeepSeekModel(settings) as model:
        plan = await Planner(
            model, max_attempts=attempts, timeout_seconds=settings.timeout_seconds,
        ).plan(query)
        metadata = {
            "plannerMode": "deepseek_live", "model": settings.model,
            "requestCount": model.request_count, "lastResponseUsage": model.last_usage,
            "elapsedMs": round((time.perf_counter() - start) * 1000),
            "planValidated": True, "agentsExecuted": False, "aipUsed": False,
        }
    return plan, metadata


def save_plan(plan, metadata, output_root=None):
    root = output_root if output_root is not None else Path(__file__).resolve().parent / "outputs"
    output = root / datetime.now().strftime("live-planner-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    for name, data in (("plan.json", plan), ("run_info.json", metadata)):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description="DeepSeek 真实规划；不是完整科研执行或 AIP 互联")
    parser.add_argument("--query", help="研究需求；省略时在终端输入")
    parser.add_argument("--max-attempts", type=int, choices=(1, 2), default=2,
                        help="最多调用次数；默认格式错误最多修正一次")
    args = parser.parse_args()
    print(f"【真实规划】会消耗 API 额度，最多 {args.max_attempts} 次模型调用；不执行 Agent 或 AIP。")
    try:
        # 先检查配置，即便点击运行，也不因缺 Key 而卡在输入提示。
        from deepseek_model import DeepSeekSettings
        DeepSeekSettings.from_env()
        query = args.query if args.query is not None else input("请输入研究问题：").strip()
        plan, metadata = asyncio.run(create_plan(query, args.max_attempts))
        output = save_plan(plan, metadata)
    except ImportError:
        print("缺少调用依赖，请运行：python -m pip install -r requirements-llm.txt")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("已取消，没有继续调用模型。")
        return 1
    except Exception as exc:
        from deepseek_model import safe_failure
        print(safe_failure(exc))
        return 1
    print("真实模型计划已通过现有 JSON、字段与依赖校验。")
    if plan["needsClarification"]:
        print("需要补充信息：", plan["clarificationQuestion"])
    else:
        for task in plan["tasks"]:
            print(f"{task['subtaskId']}：{task['taskType']}；依赖 {task['dependsOn']}")
    print("计划已保存：", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
