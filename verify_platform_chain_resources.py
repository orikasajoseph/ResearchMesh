"""Verify a saved formal S1 and its public PDF/repository resources; no MQ/LLM."""
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import re
from uuid import uuid4

from platform_chain_resources import prepare_reader_resources, verify_cached_preparation
from platform_three_agent_orchestrator import (
    SCOPE, bind_business_task, digest, read_json, validate_chain, verify_saved_stage, write_json,
)

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="允许有界arXiv/GitHub请求；无模型和MQ")
    args = parser.parse_args(argv)
    output = None
    try:
        source = args.source.resolve()
        plan, state = read_json(source / "plan.json"), read_json(source / "state.json")
        if state.get("scope") != SCOPE or state.get("planSha256") != digest(plan):
            raise ValueError("FORMAL_CHAIN_PLAN_CHANGED")
        tasks = validate_chain(plan)
        row = next(row for row in state["stages"] if row["stage"] == "literature_search"
                   and row["state"] == "completed")
        _, result = verify_saved_stage(source, row, bind_business_task(tasks[0], {}))
        task = bind_business_task(tasks[1], {tasks[0]["subtaskId"]: result})
        if not args.execute:
            print("S1原始结果和哈希有效；未请求PDF/仓库资源、未连接平台或调用模型。")
            return 0
        output = ROOT / "outputs/platform-resource-checks" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "source.json", {"source": str(source), "s1ResultSha256": row["resultSha256"],
                                           "scope": "PUBLIC_RESOURCES_ONLY_NO_MQ_NO_LLM"})
        print("资源核验记录：", output, flush=True)
        bundle = asyncio.run(prepare_reader_resources(task, result, cache_dir=ROOT / "outputs/pdf-cache",
            checkpoint=lambda value: write_json(output / "resource_attempt.json", value)))
        verify_cached_preparation(bundle, task, result, cache_dir=ROOT / "outputs/pdf-cache")
        write_json(output / "input_resolution.json", bundle)
        print("资源核验通过；未重跑S1，未发送Reader任务，模型调用0次。")
        print("可复用证据：", output / "input_resolution.json")
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,100}", str(error)) else type(error).__name__
        if output is not None:
            write_json(output / "failure.json", {"code": code, "modelCalls": 0,
                                                "platformTasksSent": 0})
        print("资源核验停止：", code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
