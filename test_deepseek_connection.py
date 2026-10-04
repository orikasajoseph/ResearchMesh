"""手动连通性检查：有 Key 才联网，一次短请求会消耗 API 额度。"""

import asyncio


async def check():
    from deepseek_model import DeepSeekModel, DeepSeekSettings
    settings = DeepSeekSettings.from_env()
    async with DeepSeekModel(settings) as model:
        await model.check_connection()
    # 不显示原始服务回复，防止诊断输出携带不应共享的信息。
    print("DeepSeek 连通性检查通过：收到非空、正常结束的真实模型回复。")
    print("只验证接口可用；未执行 Planner、科研 Agent 或 AIP。")


def main():
    print("【真实接口检查】最多一次短请求；未配置 Key 时不联网。")
    try:
        asyncio.run(check())
    except ImportError:
        print("缺少调用依赖，请运行：python -m pip install -r requirements-llm.txt")
        return 1
    except Exception as exc:
        from deepseek_model import safe_failure
        print(safe_failure(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
