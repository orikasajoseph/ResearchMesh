"""One real local AIP S1→S2→S3 run; default is a free environment preflight.

The deterministic Planner plan contains only the QLoRA title. S1 supplies every
PDF and repository URL. No paid stage or whole-chain retry is automatic.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from acps_sdk.aip import AipRpcClient
from dotenv import dotenv_values
import httpx

from aip_lifecycle import sdk_sender
from aip_orchestrator import AipOrchestrator
from planner import Planner
from resolver.local_aip import LocalAipResolver
from run_aip_literature import live_partner
from run_aip_repository import save_json
from run_literature import prepare_live_wrapper


ROOT = Path(__file__).resolve().parent
TITLE = "QLoRA: Efficient Finetuning of Quantized LLMs"


class QLoRAPlanModel:
    async def generate(self, instructions, user_query):
        return json.dumps({"schemaVersion": "0.2", "userQuery": user_query,
            "needsClarification": False, "clarificationQuestion": None, "tasks": [
            {"schemaVersion": "0.2", "subtaskId": "S1", "taskType": "literature_search",
             "goal": "Find QLoRA and its verified resources", "input": {"query": TITLE},
             "constraints": {"maxResults": 1}, "outputSchema": "paper_records.v1",
             "requiredSkill": "literature.search", "dependsOn": []},
            {"schemaVersion": "0.2", "subtaskId": "S2", "taskType": "paper_read",
             "goal": "Read the S1 QLoRA PDF and retain paper evidence", "input": {"papers": []},
             "constraints": {"maxPapers": 1}, "outputSchema": "paper_notes.v1",
             "requiredSkill": "paper.analyze", "dependsOn": ["S1"]},
            {"schemaVersion": "0.2", "subtaskId": "S3", "taskType": "repository_analyze",
             "goal": "Compare S2 paper evidence with static repository evidence without claiming reproduction",
             "input": {"papers": [], "paperNotes": [], "repositoryUrls": []},
             "constraints": {"maxRepositories": 1, "executeCode": False,
                             "allowRepositorySearch": False, "requirePaperNotes": True},
             "outputSchema": "code_records.v1", "requiredSkill": "repository.analyze",
             "dependsOn": ["S2"]}]})


def preflight(reader_python: Path, repository_python: Path, runtime_home: Path):
    wrapper, config = prepare_live_wrapper(ROOT / "agents/literature")
    if (config.search_provider != "semantic-scholar" or not config.openalex_fallback
            or config.max_retries != 0 or not config.resolve_resources):
        raise ValueError("LITERATURE_FALLBACK_PREFLIGHT_FAILED")
    if not reader_python.is_file() or not repository_python.is_file():
        raise ValueError("PARTNER_PYTHON_NOT_FOUND")
    if not (runtime_home / "huggingface").is_dir():
        raise ValueError("READER_EMBEDDING_CACHE_MISSING")
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        if not (os.environ.get(name) or values.get(name) or "").strip():
            raise ValueError("MISSING_DEEPSEEK_CONFIGURATION")
    reader_check = subprocess.run([str(reader_python), "-c",
        "import sys,importlib.util; print(sys.version_info.major,sys.version_info.minor,*[int(importlib.util.find_spec(x) is not None) for x in ('paperqa','torch','sentence_transformers','pypdf')])"],
        cwd=ROOT, capture_output=True, text=True, timeout=45, check=False)
    if reader_check.returncode != 0 or reader_check.stdout.strip() != "3 13 1 1 1 1":
        raise ValueError("READER_RUNTIME_UNAVAILABLE")
    repository_check = subprocess.run([str(repository_python), "-c",
        "import codewiki; print('ready')"], cwd=ROOT, capture_output=True, text=True,
        timeout=30, check=False)
    if repository_check.returncode != 0 or repository_check.stdout.strip() != "ready":
        raise ValueError("REPOSITORY_RUNTIME_UNAVAILABLE")
    return {"literatureKeyConfigured": bool(config.api_key), "literatureProvider": config.search_provider,
            "openalexFallback": config.openalex_fallback, "resourceResolverEnabled": config.resolve_resources,
            "readerPythonVersion": "3.13", "readerCacheAvailable": True,
            "repositoryRuntimeAvailable": True, "deepseekConfigured": True,
            "networkAttempted": False, "modelCalled": False}


async def execute(plan, endpoints):
    wire, clients, mapping = {}, [], {}
    def traced_client_type(events):
        class TracedClient(AipRpcClient):
            async def _send_request(self, command):
                response = await super()._send_request(command)
                events.append({"command": command.command.value,
                               "state": response.result.status.state.value,
                               "taskId": command.taskId})
                return response
        return TracedClient
    try:
        for skill, (url, aic, timeout, poll) in endpoints.items():
            wire[skill] = []
            client = traced_client_type(wire[skill])(url, "local-three-agent-leader",
                identity_binding_enabled=False,
                transport=httpx.AsyncHTTPTransport(trust_env=False))
            clients.append(client)
            mapping[skill] = (aic, sdk_sender(client), timeout, poll)
        outcome = await AipOrchestrator(LocalAipResolver(mapping),
                                        task_timeout_seconds=700).run_plan(plan)
        return outcome, wire
    finally:
        for client in clients:
            await client.close()


def summarize(outcome, wire, elapsed):
    records, results = outcome["executionRecords"], outcome["results"]
    by_type = {result["taskType"]: result for result in results}
    literature = by_type.get("literature_search") or {}
    reader = by_type.get("paper_read") or {}
    repository = by_type.get("repository_analyze") or {}
    paper = literature.get("items", [{}])[0].get("data", {}) if literature.get("items") else {}
    note = reader.get("items", [{}])[0].get("data", {}) if reader.get("items") else {}
    code = repository.get("items", [{}])[0].get("data", {}) if repository.get("items") else {}
    return {"elapsedSeconds": round(elapsed, 3), "provider": paper.get("provider"),
            "paperTitle": paper.get("title"), "pdfUrl": paper.get("pdfUrl"),
            "repositoryUrls": paper.get("repositoryUrls", []),
            "paperMethod": note.get("method"), "paperFindings": note.get("findings"),
            "paperLimitations": note.get("limitations"),
            "paperEvidenceCount": len(note.get("evidence") or []),
            "codeEvidenceCount": len(code.get("evidence") or []),
            "paperCodeRelationCount": len(code.get("paperCodeRelations") or []),
            "stages": [{"subtaskId": record["subtaskId"], "taskId": record.get("aipTaskId"),
                        "aipState": record.get("aipState"), "leaderValidated": record["succeeded"],
                        "executionMs": record.get("executionMs"), "error": record.get("error"),
                        "commands": [item["command"] for item in wire.get(record["requiredSkill"], [])]}
                       for record in records],
            "allPassed": len(records) == 3 and all(record["succeeded"] and record["aipState"] == "completed"
                                                    for record in records),
            "targetCodeExecuted": False, "paperReproductionClaimed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader-python", type=Path,
                        default=ROOT.parent / "paper-qa-clean/.venv/Scripts/python.exe")
    parser.add_argument("--repository-python", type=Path,
                        default=ROOT / "agents/repository/.venv/Scripts/python.exe")
    parser.add_argument("--reader-runtime-home", type=Path, default=Path.home() / ".cache")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--health", action="store_true", help="Start three local Partners and query health only")
    args = parser.parse_args(argv)
    reader_python = args.reader_python.resolve()
    repository_python = args.repository_python.resolve()
    runtime_home = args.reader_runtime_home.resolve()
    check = preflight(reader_python, repository_python, runtime_home)
    plan = asyncio.run(Planner(QLoRAPlanModel(), max_attempts=1).plan(
        "Find QLoRA, read the paper, and analyze its official code repository"))
    if not args.execute and not args.health:
        print(json.dumps({"preflight": check, "planTaskTypes": [task["taskType"] for task in plan["tasks"]],
                          "plannerInputsHaveUrls": False}, ensure_ascii=False))
        return 0
    output = ROOT / "outputs" / datetime.now().strftime("aip-research-three-agent-qlora-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    for stage in ("literature", "reader", "repository"):
        (output / stage).mkdir()
    save_json(output / "plan.json", plan)
    save_json(output / "preflight.json", check)
    reader_env = {"RESEARCHMESH_READER_DYNAMIC_INPUT": "1",
        "RESEARCHMESH_READER_PYTHON": str(reader_python),
        "RESEARCHMESH_READER_ROOT": str(ROOT / "agents/paper_reader"),
        "RESEARCHMESH_READER_ENV_FILE": str(ROOT / ".env"),
        "RESEARCHMESH_READER_RUNTIME_HOME": str(runtime_home)}
    if args.health:
        with ExitStack() as stack:
            stack.enter_context(live_partner(ROOT / "agents/literature", output / "literature"))
            stack.enter_context(live_partner(ROOT / "agents/paper_reader", output / "reader",
                factory="aip_reader_partner:create_live_app", mode="local-reader-live",
                extra_env=reader_env, aic_prefix="local-reader-"))
            stack.enter_context(live_partner(ROOT / "agents/repository", output / "repository",
                factory="aip_repository_partner:create_live_app", mode="local-repository-live",
                python_executable=repository_python, aic_prefix="local-repository-"))
        save_json(output / "health.json", {"allPartnersHealthy": True, "taskSent": False,
                                          "modelCalled": False, "externalNetworkUsed": False})
        print(json.dumps({"recordDir": str(output), "allPartnersHealthy": True,
                          "taskSent": False, "modelCalled": False}))
        return 0
    started = time.perf_counter()
    with ExitStack() as stack:
        literature = stack.enter_context(live_partner(ROOT / "agents/literature", output / "literature"))
        reader = stack.enter_context(live_partner(ROOT / "agents/paper_reader", output / "reader",
            factory="aip_reader_partner:create_live_app", mode="local-reader-live",
            extra_env=reader_env, aic_prefix="local-reader-"))
        repository = stack.enter_context(live_partner(ROOT / "agents/repository", output / "repository",
            factory="aip_repository_partner:create_live_app", mode="local-repository-live",
            python_executable=repository_python, aic_prefix="local-repository-"))
        endpoints = {"literature.search": (*literature, 130, .5),
                     "paper.analyze": (*reader, 675, 1),
                     "repository.analyze": (*repository, 180, .5)}
        outcome, wire = asyncio.run(execute(plan, endpoints))
    save_json(output / "leader_result.json", outcome)
    save_json(output / "aip_events.json", wire)
    summary = summarize(outcome, wire, time.perf_counter() - started)
    summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
    save_json(output / "acceptance.json", summary)
    print(json.dumps({"recordDir": str(output), "acceptance": summary}, ensure_ascii=False))
    return 0 if summary["allPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
