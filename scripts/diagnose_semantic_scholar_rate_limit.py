"""独立冷启动：一个 Provider，等待两秒，只搜索一次 limit=1，不重试、不调用模型。"""

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from literature_adapter import call_wrapper  # noqa: E402
from run_literature import prepare_live_wrapper  # noqa: E402


async def diagnose():
    wrapper, config = prepare_live_wrapper(ROOT / "agents" / "literature")
    provider = wrapper.SemanticScholarProvider(config=config, request_source="cold-start-diagnostic",
                                               subtask_id="cold-S1")
    agent = wrapper.LiteratureAgent(provider)
    payload = {"schemaVersion": "0.2", "subtaskId": "cold-S1", "taskType": "literature_search",
               "goal": "独立单次冷启动限流诊断", "input": {"query": "world model"},
               "constraints": {"maxResults": 1}, "outputSchema": "paper_records.v1"}
    print("冷启动：Provider 已创建，等待 2 秒；只检索一次 limit=1，不重试、不调用模型。", flush=True)
    await asyncio.sleep(2)
    started = time.perf_counter()
    outcome = await call_wrapper(payload, "cold-start", agent, wrapper.LiteratureSearchRequest)
    diagnostics = provider.http_diagnostics()
    events = diagnostics["httpRequests"]
    record = {**diagnostics, "mode": "cold-start-single-request", "llmUsed": False,
              "retryCount": 0, "durationMs": round((time.perf_counter() - started) * 1000, 3),
              "httpStatus": events[-1]["httpStatus"] if events else None,
              "endpoint": events[-1]["endpoint"] if events else None,
              "errorCode": outcome["error"]["code"] if outcome["error"] else None,
              "providerReturned": outcome["diagnostics"].get("providerReturned", 0)}
    output = ROOT / "outputs" / datetime.now().strftime("semantic-cold-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    (output / "diagnostics.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("诊断记录：", output)
    return 1 if outcome["error"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(diagnose()))
    except (EOFError, KeyboardInterrupt):
        print("已取消；不继续请求。")
        raise SystemExit(1)
    except Exception:
        # 不打印 traceback、SDK 原始请求或认证数据。
        print("诊断未完成；请在本机检查配置和依赖，不发送 .env 或认证信息。")
        raise SystemExit(1)
