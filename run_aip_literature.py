"""Saved plan -> local AIP -> real Literature Wrapper. --execute opts into API.

Default preflight performs no network. No Planner/DeepSeek/PDF/Reader/discovery.
"""
import argparse
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
from aip_lifecycle import AipTaskRunner, AipExecutionError, sdk_sender
from common.models import BUSINESS_TASK_FIELDS
from literature_adapter import AdapterError, to_wrapper_request
from planner_contract import validate_plan
from run_aip_business import save_json
from run_literature import prepare_live_wrapper
from local_process import stop_owned_process

ROOT = Path(__file__).resolve().parent


def select_search_task(plan, subtask_id="S1"):
    validate_plan(plan)
    if plan["needsClarification"]:
        raise AdapterError("PLAN_NEEDS_CLARIFICATION")
    task = next((t for t in plan["tasks"] if t["subtaskId"] == subtask_id), None)
    if task is None:
        raise AdapterError("SELECTED_TASK_NOT_FOUND")
    if task["taskType"] != "literature_search" or task["dependsOn"]:
        raise AdapterError("ONLY_INDEPENDENT_LITERATURE_SUPPORTED")
    payload = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
    to_wrapper_request(payload, "preflight")
    return payload


async def invoke_business(payload, url, aic, *, transport=None, timeout_seconds=135, poll_seconds=0.5,
                          expected_task_type="literature_search"):
    parsed = httpx.URL(url)
    if (parsed.scheme != "http" or parsed.host != "127.0.0.1" or parsed.path != "/rpc"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise AdapterError("LOOPBACK_RPC_REQUIRED")
    if expected_task_type not in {"literature_search", "paper_read", "repository_analyze"} or payload["taskType"] != expected_task_type:
        raise AdapterError("UNEXPECTED_TASK_TYPE")
    messages = []
    class TracedClient(AipRpcClient):
        async def _send_request(self, command):
            response = await super()._send_request(command)
            messages.append({"command": command.model_dump(mode="json", exclude_none=True),
                             "response": response.model_dump(mode="json", exclude_none=True)})
            return response
    client = TracedClient(url, "local-literature-leader", identity_binding_enabled=False,
                         transport=transport if transport is not None else httpx.AsyncHTTPTransport(trust_env=False))
    try:
        runner = AipTaskRunner(sdk_sender(client), "local-literature-leader", aic,
                               timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
        try:
            execution = await runner.run(payload, str(uuid4()))
        except AipExecutionError as error:
            outcome = {"execution": None, "error": {"code": error.code},
                       "trace": getattr(error, "trace", {})}
        else:
            outcome = {"execution": execution, "error": None}
        outcome["aipMessages"] = messages
        return outcome
    finally:
        await client.close()


async def invoke_literature(payload, url, aic, **kwargs):
    return await invoke_business(payload, url, aic, **kwargs)


@contextmanager
def live_partner(wrapper_root, output, *, factory="aip_literature_partner:create_live_app",
                 mode="local-literature-live", extra_env=None, aic_prefix="local-literature", python_executable=None):
    # Preserve Zhang's isolated-runtime extension without changing the default
    # interpreter or importing another Resolver/CLI into the business service.
    python = Path(python_executable or sys.executable).resolve()
    if not python.is_file():
        raise ValueError("LOCAL_PARTNER_PYTHON_NOT_FOUND")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    aic = aic_prefix + "-" + str(uuid4())
    env = dict(os.environ, RESEARCHMESH_LOCAL_PARTNER_AIC=aic,
               RESEARCHMESH_AIP_WRAPPER_ROOT=str(wrapper_root), RESEARCHMESH_AIP_RECORD_DIR=str(output),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    if extra_env:
        env.update(extra_env)
    with (output / "partner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(python), "-B", "-m", "uvicorn", factory,
                                    "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                                   cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise AdapterError("LOCAL_PARTNER_START_FAILED")
                    try:
                        response = client.get(base + "/health")
                        response.raise_for_status()
                        health = response.json()
                        if (health.get("aic") != aic or health.get("mode") != mode
                                or health.get("backgroundExecution") is not True):
                            raise AdapterError("LOCAL_PARTNER_HEALTH_MISMATCH")
                        break
                    except httpx.HTTPError:
                        if time.monotonic() >= deadline:
                            raise AdapterError("LOCAL_PARTNER_START_TIMEOUT")
                        time.sleep(0.1)
            yield base + "/rpc", aic
        finally:
            stop_owned_process(process)


def assess_live_run(outcome, backend):
    execution = outcome.get("execution")
    protocol = bool(execution and outcome.get("error") is None and execution["state"] == "completed")
    backend_ok = bool(backend and backend.get("error") is None and backend.get("result") is not None)
    matched = bool(protocol and backend_ok and execution["result"] == backend["result"])
    diagnostics = (backend or {}).get("diagnostics", {})
    http_events = diagnostics.get("httpRequests", [])
    observed = bool(diagnostics.get("httpObservationAvailable") is True and http_events)
    successful_http = observed and http_events[-1].get("httpStatus") == 200
    # Accept the preview Wrapper's explicit fallback provenance, not just its
    # provider label. A successful Semantic Scholar request cannot certify OA.
    providers = {item.get("data", {}).get("provider") for item in execution["result"]["items"]} if protocol else set()
    openalex_http = bool(diagnostics.get("providerUsed") == "openalex"
                        and diagnostics.get("provider") == "openalex"
                        and type(diagnostics.get("openalexRequests")) is int and diagnostics["openalexRequests"] >= 1
                        and diagnostics.get("openalexHttpStatus") == 200
                        and (not providers or providers == {"openalex"}))
    semantic_result = (diagnostics.get("provider", "semantic-scholar") == "semantic-scholar"
                       and diagnostics.get("providerUsed", "semantic-scholar") == "semantic-scholar"
                       and providers <= {None, "semantic-scholar"})
    verified_http = bool((successful_http and semantic_result) or openalex_http)
    non_mock = bool(execution and execution.get("simulatedResult") is False)
    papers = len(execution["result"]["items"]) if protocol else 0
    return {"protocolCompleted": protocol, "backendResultMatchesAipProduct": matched,
            "httpObservationAvailable": bool(observed or openalex_http), "semanticHttp200Observed": bool(successful_http),
            "openalexHttp200Observed": openalex_http,
            "returnedPaperCount": papers, "businessReady": bool(matched and verified_http and non_mock and papers),
            "researchSucceeded": bool(matched and verified_http and non_mock and papers),
            "emptyResult": bool(matched and papers == 0),
            "passed": bool(matched and verified_http and non_mock),
            "scope": "LOCAL_AIP_LIVE_LITERATURE_ONLY", "realSourceRequested": True if observed or openalex_http else None,
            "llmCalled": False, "paperReadingExecuted": False, "pdfDownloaded": False,
            "repositoryAnalysisExecuted": False, "platformDiscoveryUsed": False,
            "tlsIdentityVerified": False, "fixtureReplay": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--subtask", default="S1")
    parser.add_argument("--wrapper-root", type=Path, default=ROOT / "agents/literature")
    parser.add_argument("--execute", action="store_true", help="明确允许 Partner 调用 Semantic Scholar API")
    parser.add_argument("--record-dir", type=Path, help="指定新的记录目录；已有目录不会覆盖")
    args = parser.parse_args(argv)
    output = args.record_dir.resolve() if args.record_dir else ROOT / "outputs" / datetime.now().strftime("aip-literature-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    try:
        plan_path, wrapper_root = args.plan.resolve(), args.wrapper_root.resolve()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        payload = select_search_task(plan, args.subtask)
        if importlib.metadata.version("acps-sdk") != "2.2.0":
            raise AdapterError("SDK_220_REQUIRED")
        prepare_live_wrapper(wrapper_root)  # local-only presence/import check, no provider creation
        save_json(output / "plan.json", plan)
        save_json(output / "payload.json", payload)
        save_json(output / "environment.json", {
            "python": sys.version, "sdkVersion": "2.2.0", "wireSchemaVersion": "0.2",
            "planSource": str(plan_path), "wrapperRoot": str(wrapper_root),
            "apiKeyConfigured": True, "executeRequested": args.execute,
            "notExecutedSubtasks": [t["subtaskId"] for t in plan["tasks"] if t["subtaskId"] != args.subtask],
            "wrapperFiles": {str(p.relative_to(wrapper_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted((wrapper_root / "researchmesh_literature").glob("*.py"))}})
        if not args.execute:
            save_json(output / "preflight.json", {"passed": True, "networkAttempted": False,
                                                   "aipUsed": False, "llmCalled": False,
                                                   "semanticApiCalled": False, "executeRequested": False})
            print("预检通过：计划、v0.2 任务、Wrapper 与本机 Semantic Scholar Key 已检查；未联网。")
            print("加 --execute 才运行真实 AIP 检索；不调用 DeepSeek、不运行阅读任务。")
            print("记录：", output)
            return 0
        print("【真实本地 AIP + 真实文献检索】读取已有计划，不调用 DeepSeek、Reader 或梧桐。", flush=True)
        print("只执行", args.subtask, "；Partner 后台检索，Leader 查询状态；限流时沿用已有有界重试。", flush=True)
        with live_partner(wrapper_root, output) as (url, aic):
            outcome = asyncio.run(invoke_literature(payload, url, aic))
        save_json(output / "outcome.json", outcome)
        backend_path = output / "backend_outcome.json"
        backend = json.loads(backend_path.read_text(encoding="utf-8")) if backend_path.is_file() else None
        acceptance = assess_live_run(outcome, backend)
        acceptance["partnerStopped"] = True
        save_json(output / "acceptance.json", acceptance)
        execution = outcome.get("execution")
        result = execution["result"] if execution else None
        lines = ["# ResearchMesh 本地 AIP 真实文献检索", "",
                 "> 本机官方 AIP RPC 调用真实 Literature Wrapper；未执行模型规划、全文阅读、ADP 或梧桐。", "",
                 "原研究需求：" + plan["userQuery"], "",
                 f"本轮只执行：{args.subtask} / literature.search。", "",
                 "协议完成：" + str(acceptance["protocolCompleted"]),
                 "科研可用论文结果：" + str(acceptance["businessReady"]), ""]
        if result:
            lines.extend([result["summary"], ""])
            for index, item in enumerate(result["items"], 1):
                data = item["data"]
                lines.extend([f"## {index}. {data['title']}", "",
                              "论文链接：" + (data.get("url") or "未提供"),
                              "DOI：" + (data.get("doi") or "未提供"),
                              "年份：" + str(data.get("year")), ""])
            lines.extend(result["warnings"])
        else:
            error = ((backend or {}).get("error") or outcome.get("error") or {}).get("code", "NO_VERIFIED_RESULT")
            lines.extend(["未成功：" + error, ""])
        lines.extend(["", "关键词检索结果不等于科研质量核验；未部署公网或完成平台身份/发现验收。"])
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("protocolCompleted：", acceptance["protocolCompleted"])
        print("businessReady：", acceptance["businessReady"])
        if acceptance["passed"]:
            print("真实检索结果：", acceptance["returnedPaperCount"], "篇；未阅读全文。")
        else:
            print("未完成：", ((backend or {}).get("error") or outcome.get("error") or {}).get("code", "EVIDENCE_INCOMPLETE"))
            requests = (backend or {}).get("diagnostics", {}).get("httpRequests", [])
            if requests and requests[-1].get("transportError"):
                print("网络原因：", requests[-1]["transportError"], "；查看 backend_outcome.json 的脱敏诊断。")
        print("临时 Partner 已停止。记录：", output)
        return 0 if acceptance["passed"] else 1
    except (Exception, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, (AdapterError, AipExecutionError)) else type(error).__name__
        save_json(output / "failure.json", {"error": code, "executeRequested": args.execute,
                                            "llmCalled": False, "partnerStopped": None})
        print("未完成：", code, "；记录：", output)
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
