"""One-command, loopback-only integration with Wu's unmodified delivery.

Reads a saved plan. Never calls a model, a research backend or platform discovery.
The owned Partner process is stopped in finally; protocol success is not research success.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
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
from common.models import BUSINESS_TASK_FIELDS
from planner_contract import validate_plan
from result_contract import validate_result

ROOT = Path(__file__).resolve().parent
DEFAULT_WU = ROOT.parent / "wu_delivery(1)" / "ResearchMesh_wu_delivery_v0.2_local"
DEFAULT_PLAN = ROOT / "outputs/live-planner-20261003-124233-600820/plan.json"


def select_task(plan, subtask_id="S1"):
    validate_plan(plan)
    task = next((task for task in plan["tasks"] if task["subtaskId"] == subtask_id), None)
    if task is None:
        raise ValueError("SELECTED_TASK_NOT_FOUND")
    if task["taskType"] != "literature_search":
        raise ValueError("ONLY_LITERATURE_SUPPORTED")
    if task["dependsOn"]:
        raise ValueError("DEPENDENCIES_NOT_EXECUTED")
    return task, {key: task[key] for key in BUSINESS_TASK_FIELDS}


def load_delivery(root):
    root = Path(root).resolve()
    for name in ("structured.py", "adapter.py", "contracts.py", "partner.py"):
        if not (root / "wu_delivery" / name).is_file():
            raise ValueError("WU_DELIVERY_INCOMPLETE")
    sys.path.insert(0, str(root))
    import wu_delivery
    if Path(wu_delivery.__file__).resolve().parent != root / "wu_delivery":
        raise ValueError("WU_IMPORT_PATH_CONFLICT")
    from wu_delivery.structured import ResearchClient
    from wu_delivery.adapter import invoke_for_planner
    from wu_delivery.contracts import ResearchTask
    return ResearchClient, invoke_for_planner, ResearchTask


def assess_outcome(outcome, payload, events):
    """Validate local outcome without adding status fields to the business JSON."""
    result = outcome.get("result")
    valid = False
    if result is not None:
        validate_result(result, payload)
        valid = True
    warnings = list(outcome.get("warnings", [])) + (result or {}).get("warnings", [])
    transport_only = any(w.startswith("TRANSPORT_TEST_ONLY") for w in warnings)
    # Require the complete handshake even if an adapter accepts direct completed.
    handshake = [(event["command"], event["state"]) for event in events]
    protocol_ok = (outcome.get("protocolCompleted") is True and outcome.get("error") is None
                   and handshake == [("start", "awaiting-completion"), ("complete", "completed")])
    return {
        "protocolVerified": protocol_ok,
        "businessEnvelopeValidated": valid,
        "transportTestOnly": transport_only,
        "researchExecuted": False,
        "researchSucceeded": False,
        "localAcceptancePassed": protocol_ok and valid and transport_only and outcome.get("businessReady") is False,
        "note": "通信完成不代表科研完成；此入口只启动吴的默认传输测试 Partner。",
    }


async def invoke_local(payload, url, partner_aic, delivery_root, *, transport=None):
    parsed = httpx.URL(url)
    if parsed.scheme != "http" or parsed.host != "127.0.0.1" or parsed.path != "/rpc":
        raise ValueError("LOOPBACK_RPC_REQUIRED")
    ResearchClient, invoke_for_planner, ResearchTask = load_delivery(delivery_root)
    request = ResearchTask.model_validate(payload)
    session_id, task_id = str(uuid4()), str(uuid4())
    events = []

    class TracedClient(ResearchClient):
        async def _send_request(self, command):
            sent_at = datetime.now(timezone.utc).isoformat()
            response = await super()._send_request(command)
            result = response.result
            if (result is None or result.taskId != task_id or result.sessionId != session_id
                    or result.senderId != partner_aic or result.senderRole != "partner"):
                raise ValueError("RESPONSE_IDENTITY_OR_CORRELATION_MISMATCH")
            events.append({
                "command": command.command.value, "messageId": command.id,
                "responseMessageId": result.id, "taskId": task_id, "sessionId": session_id,
                "senderAic": result.senderId, "state": result.status.state.value,
                "sentAt": sent_at, "receivedAt": datetime.now(timezone.utc).isoformat(),
            })
            return response

    client = TracedClient(
        partner_url=url, leader_id="example-leader-aic", identity_binding_enabled=False,
        transport=transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False),
    )
    try:
        outcome = await invoke_for_planner(client, request, session_id=session_id, task_id=task_id)
        data = outcome.to_dict()
        # The adapter may include validation payloads in error messages. Never persist them.
        if data.get("error"):
            data["error"] = data["error"].split(":", 1)[0]
        assessment = assess_outcome(data, payload, events)
        return data, events, assessment
    finally:
        await client.close()


@contextmanager
def local_partner(delivery_root, output):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    expected_aic = "local-wu-test-" + str(uuid4())
    # A unique marker prevents accepting another process if the port was taken meanwhile.
    env = dict(os.environ, PARTNER_AIC=expected_aic, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    with (output / "partner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "uvicorn", "wu_delivery.partner:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=str(delivery_root), env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("PARTNER_START_FAILED")
                    try:
                        response = client.get(url + "/health")
                        response.raise_for_status()
                        health = response.json()
                        if health.get("aic") != expected_aic or health.get("mode") != "local-only":
                            raise RuntimeError("PARTNER_HEALTH_IDENTITY_MISMATCH")
                        break
                    except httpx.HTTPError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("PARTNER_START_TIMEOUT")
                        time.sleep(0.1)
            yield url + "/rpc", expected_aic
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
    parser = argparse.ArgumentParser(description="已保存计划 → 吴的本地 AIP Partner；不调用模型或论文 API")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--wu-root", type=Path, default=DEFAULT_WU)
    parser.add_argument("--subtask", default="S1")
    args = parser.parse_args()
    output = ROOT / "outputs" / datetime.now().strftime("wu-aip-local-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    print("【本地 AIP 联调】读取已有计划；不调用 LLM、Semantic Scholar 或梧桐。", flush=True)
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError("PYTHON_311_REQUIRED")
        if importlib.metadata.version("acps-sdk") != "2.2.0":
            raise RuntimeError("SDK_220_REQUIRED")
        plan_path, delivery_root = args.plan.resolve(), args.wu_root.resolve()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        task, payload = select_task(plan, args.subtask)
        load_delivery(delivery_root)
        save_json(output / "plan.json", plan)
        save_json(output / "request.json", payload)
        source_hashes = {str(path.relative_to(delivery_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted((delivery_root / "wu_delivery").glob("*.py"))}
        save_json(output / "environment.json", {
            "python": sys.version, "sdkVersion": "2.2.0", "planSource": str(plan_path),
            "deliveryRoot": str(delivery_root), "deliverySourceHashes": source_hashes,
            "mode": "local-transport-only", "llmCalled": False, "platformDiscoveryUsed": False,
            "tlsIdentityVerified": False, "researchExecuted": False,
        })
        with local_partner(delivery_root, output) as (url, aic):
            outcome, events, assessment = asyncio.run(invoke_local(payload, url, aic, delivery_root))
        save_json(output / "outcome.json", outcome)
        save_json(output / "aip_events.json", events)
        save_json(output / "acceptance.json", {**assessment, "partnerStopped": True,
            "selectedSubtask": task["subtaskId"], "skill": task["requiredCapability"],
            "notExecutedSubtasks": [t["subtaskId"] for t in plan["tasks"] if t["subtaskId"] != task["subtaskId"]]})
        passed = assessment["localAcceptancePassed"]
        report = "# ResearchMesh × 吴：本地 AIP 联调\n\n"
        report += "结论：" + ("传输测试通过；尚未执行论文检索。" if passed else "传输验收未通过，请检查 outcome.json。") + "\n\n"
        report += f"选择任务：{task['subtaskId']} / {task['requiredCapability']}；协议耗时：{outcome['latencyMs']} ms。\n\n"
        report += "过程：" + " → ".join(f"{e['command']} / {e['state']}" for e in events) + "\n\n"
        report += "只运行选中的文献检索传输任务；未运行阅读、代码分析、ADP、平台认证或 AMP。\n\n"
        report += "临时 Partner 已关闭。关闭 TLS 身份绑定仅用于本机回环测试；不能用此入口部署公网。\n"
        (output / "report.md").write_text(report, encoding="utf-8")
        print("通信验收：", "通过" if passed else "未通过")
        print("protocolCompleted：", outcome["protocolCompleted"])
        print("businessReady：", outcome["businessReady"])
        print("科研执行：未执行；本轮只验证传输。")
        print("临时 Partner 已停止。记录：", output)
        return 0 if passed else 1
    except KeyboardInterrupt:
        save_json(output / "failure.json", {"error": "CANCELED_BY_USER", "researchExecuted": False})
        print("已停止联调；自启动的 Partner 会被清理。")
        return 130
    except Exception as error:
        # Do not print model validation inputs or credentials from underlying exceptions.
        code = str(error) if str(error) in {
            "SELECTED_TASK_NOT_FOUND", "ONLY_LITERATURE_SUPPORTED", "DEPENDENCIES_NOT_EXECUTED",
            "WU_DELIVERY_INCOMPLETE", "WU_IMPORT_PATH_CONFLICT", "PARTNER_START_FAILED",
            "PARTNER_HEALTH_IDENTITY_MISMATCH", "PARTNER_START_TIMEOUT", "PYTHON_311_REQUIRED", "SDK_220_REQUIRED",
        } else type(error).__name__
        save_json(output / "failure.json", {"error": code, "researchExecuted": False})
        print("联调失败：", code, "；请检查计划路径、吴交付目录和项目 .venv。")
        print("记录：", output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
