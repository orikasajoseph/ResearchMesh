"""One-command local SDK RPC + v0.2 literature->reader fixture validation.

No .env, model, search API, PDF or platform calls. Owned server is loopback only.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from acps_sdk.aip import AipRpcClient
from aip_lifecycle import AipTaskRunner, sdk_sender
from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input
from planner_contract import validate_plan

ROOT = Path(__file__).resolve().parent


def fixture_plan():
    return {"schemaVersion": "0.2", "userQuery": "【模拟】本地 AIP 检索→阅读接口联调",
            "needsClarification": False, "clarificationQuestion": None, "tasks": [
        {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
         "goal": "【模拟】验证检索消息", "input": {"query": "mock AIP test"},
         "constraints": {"maxResults": 1}, "outputSchema": "paper_records.v1",
         "requiredSkill": "literature.search", "dependsOn": []},
        {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
         "goal": "【模拟】验证单篇阅读消息", "input": {"papers": []},
         "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
         "requiredSkill": "paper.analyze", "dependsOn": ["S1"]}]}


async def run_fixture_chain(url, partner_aic, *, transport=None):
    parsed = httpx.URL(url)
    if (parsed.scheme != "http" or parsed.host != "127.0.0.1" or parsed.path != "/rpc"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("LOOPBACK_RPC_REQUIRED")
    wire = []

    class TracedClient(AipRpcClient):
        async def _send_request(self, command):
            response = await super()._send_request(command)
            wire.append({"command": command.model_dump(mode="json", exclude_none=True),
                         "response": response.model_dump(mode="json", exclude_none=True)})
            return response

    plan = fixture_plan()
    validate_plan(plan)
    session_id, leader_aic = str(uuid4()), "local-business-leader"
    client = TracedClient(url, leader_aic, identity_binding_enabled=False,
                         transport=transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False))
    results, executions, payloads = {}, [], []
    try:
        for task in plan["tasks"]:
            bound = bind_task_input(task, results)
            payload = {key: deepcopy(bound[key]) for key in BUSINESS_TASK_FIELDS}
            runner = AipTaskRunner(sdk_sender(client), leader_aic, partner_aic,
                                   allow_mock_results=True)
            execution = await runner.run(payload, session_id)
            results[task["subtaskId"]] = execution["result"]
            executions.append(execution)
            payloads.append(payload)
    finally:
        await client.close()
    checks = {
        "bothTasksConfirmed": all(e["state"] == "completed" for e in executions) and len(executions) == 2,
        "standardHandshake": all([(x["command"], x["state"]) for x in e["events"]] ==
                                  [("start", "awaiting-completion"), ("complete", "completed")] for e in executions),
        "businessV02Unchanged": all(p["schemaVersion"] == "0.2" for p in payloads),
        "onlySevenBusinessFields": all(set(p) == set(BUSINESS_TASK_FIELDS) for p in payloads),
        "readerBoundFromSearch": payloads[1]["input"]["papers"][0]["paperId"] == results["S1"]["items"][0]["data"]["paperId"],
        "readerSourcePreserved": results["S2"]["items"][0]["data"]["source"] == payloads[1]["input"]["papers"][0],
        "bothResultsExplicitlySimulated": all(e["simulatedResult"] for e in executions),
        "protocolIdsDistinctFromSubtaskIds": all(e["taskId"] != e["subtaskId"] for e in executions)
                                            and len({e["taskId"] for e in executions}) == 2,
    }
    return {"plan": plan, "payloads": payloads, "executions": executions, "aipMessages": wire,
            "acceptance": {"passed": all(checks.values()), "checks": checks,
                           "scope": "LOCAL_AIP_REAL_SDK_RPC_SIMULATED_RESEARCH_RESULTS",
                           "protocolCompleted": checks["bothTasksConfirmed"], "businessReady": False,
                           "realResearchExecuted": False, "llmCalled": False, "paperApiCalled": False,
                           "pdfRead": False, "platformDiscoveryUsed": False, "tlsIdentityVerified": False,
                           "fixtureReplay": False, "syntheticBackend": True}}


@contextmanager
def fixture_partner(output):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    aic = "local-business-fixture-" + str(uuid4())
    env = dict(os.environ, RESEARCHMESH_LOCAL_PARTNER_AIC=aic,
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    with (output / "partner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "aip_business_partner:app",
                                    "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                                   cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("LOCAL_PARTNER_START_FAILED")
                    try:
                        response = client.get(base + "/health")
                        response.raise_for_status()
                        health = response.json()
                        if health.get("aic") != aic or health.get("mode") != "local-business-fixture":
                            raise RuntimeError("LOCAL_PARTNER_HEALTH_MISMATCH")
                        break
                    except httpx.HTTPError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("LOCAL_PARTNER_START_TIMEOUT")
                        time.sleep(0.1)
            yield base + "/rpc", aic
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    output = ROOT / "outputs" / datetime.now().strftime("aip-business-fixture-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    print("【真实本地 AIP + 模拟科研结果】v0.2 检索→阅读；不调用模型、论文 API、PDF 或梧桐。", flush=True)
    try:
        if importlib.metadata.version("acps-sdk") != "2.2.0":
            raise RuntimeError("SDK_220_REQUIRED")
        with fixture_partner(output) as (url, aic):
            record = asyncio.run(run_fixture_chain(url, aic))
        record["acceptance"]["partnerStopped"] = True
        for key in ("plan", "payloads", "executions", "aipMessages", "acceptance"):
            save_json(output / (key + ".json"), record[key])
        save_json(output / "environment.json", {
            "python": sys.version, "sdkVersion": "2.2.0", "wireSchemaVersion": "0.2",
            "files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                      for name in ("run_aip_business.py", "aip_business_partner.py", "aip_lifecycle.py")}})
        (output / "report.md").write_text(
            "# ResearchMesh 本地 AIP 业务接口联调\n\n"
            "真实 SDK 消息通过本机 HTTP RPC 收发；科研条目为合成模拟数据，不是科研证据。\n\n"
            "固定测试计划 → S1 检索 → Leader 绑定 PaperRef → S2 阅读；两项能力由同一个测试 Partner 承接。\n\n"
            "两项任务分别经过 start / awaiting-completion → complete / completed；业务外壳保持 v0.2。\n\n"
            "没有调用 Zhang 真实 Wrapper、模型、Semantic Scholar、PDF、ADP 或梧桐；没有加载 .env。\n\n"
            "businessReady=false：本轮只证明业务接口和本地协议联调，不证明真实科研质量。\n\n"
            "身份绑定关闭仅限本机测试，不得发布公网；临时 Partner 已停止。\n",
            encoding="utf-8")
        print("接口联调：", "通过" if record["acceptance"]["passed"] else "未通过")
        print("S1 检索 → S2 阅读：两项 AIP 任务已确认完成（科研内容为模拟）。")
        print("protocolCompleted：", record["acceptance"]["protocolCompleted"])
        print("businessReady：False；真实科研执行：未执行。")
        print("临时 Partner 已停止。记录：", output)
        return 0 if record["acceptance"]["passed"] else 1
    except (Exception, KeyboardInterrupt) as error:
        from aip_lifecycle import AipExecutionError
        code = error.code if isinstance(error, AipExecutionError) else type(error).__name__
        # Cleanup runs in the context manager. Do not claim a verified stop if
        # startup/cleanup itself failed before returning to main.
        failure = {"error": code, "realResearchExecuted": False, "partnerStopped": None}
        if isinstance(error, AipExecutionError) and hasattr(error, "trace"):
            failure["trace"] = error.trace
        save_json(output / "failure.json", failure)
        print("未通过：", code, "；记录：", output)
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
