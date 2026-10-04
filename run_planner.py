"""统一 Provider 的真实规划入口；现有 run_deepseek_planner.py 继续可用。"""

import argparse
import asyncio
import time
from llm_provider import ProviderSettings, ProviderPlanningModel
from deepseek_model import safe_failure
from planner import Planner
from run_deepseek_planner import save_plan


async def create_plan(query):
    settings = ProviderSettings.from_env()
    start = time.perf_counter()
    async with ProviderPlanningModel(settings) as model:
        plan = await Planner(model, timeout_seconds=settings.timeout_seconds).plan(query)
        metadata = {
            "plannerMode": "live", "provider": settings.provider, "model": settings.model,
            "requestCount": model.request_count, "lastResponseUsage": model.last_usage,
            "elapsedMs": round((time.perf_counter() - start) * 1000),
            "planValidated": True, "agentsExecuted": False, "aipUsed": False,
        }
    return plan, metadata


def main():
    parser = argparse.ArgumentParser(description="真实规划：消耗模型额度，不执行 Agent/AIP")
    parser.add_argument("--query")
    args = parser.parse_args()
    try:
        ProviderSettings.from_env()
        query = args.query or input("请输入研究问题：").strip()
        plan, metadata = asyncio.run(create_plan(query))
        print("已保存：", save_plan(plan, metadata))
        print(plan["clarificationQuestion"] or "计划通过字段、skill、输入及依赖校验。")
    except (EOFError, KeyboardInterrupt):
        print("已取消")
        return 1
    except Exception as exc:
        print(safe_failure(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
