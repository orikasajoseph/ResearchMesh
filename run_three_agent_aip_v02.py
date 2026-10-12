"""ResearchMesh S1/S2/S3 local official-SDK AIP fixture chain.

NO internet, CodeWiki, PDF download, DeepSeek/LLM, ACS, ADP or MQ.
S1 is a *synthetic* literature fixture (v0.1 business envelope).
S2 uses the actual PaperReaderAgent/Reader Bridge on a no-PDF reference.
S3 uses the actual RepositoryAgent/Repository Bridge with fake local backend.
S3 depends on S1, NOT S2, so the no-PDF Reader failure does not hide the
independent Repository AIP call. Never claim this is a fully researched chain.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from typing import Any

import httpx

from result_contract import validate_result
from three_agent_dispatch_v02 import dispatch_plan

ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "examples" / "three_agent_local_fixture_v02.json"


def offline_paper_record(subtask_id: str) -> dict[str, Any]:
    """Clearly synthetic PaperRef. No fake DOI or claim of publication."""
    return {
        "schemaVersion": "0.1", "subtaskId": subtask_id,
        "taskType": "literature_search",
        "summary": "A synthetic paper reference was returned for local AIP routing only.",
        "items": [{"kind": "paper", "data": {
            "paperId": "OFFLINE-PAPER-ONLY", "title": "OFFLINE FIXTURE - not a scientific paper",
            "doi": None, "url": "https://example.org/offline-paper-reference",
            "pdfUrl": None, "repositoryUrls": [], "isMock": True,
        }}],
        "warnings": ["OFFLINE_FIXTURE_NOT_LITERATURE_SEARCH"],
    }


def create_literature_fixture_app():
    """AIP SDK Partner: real RPC handshake, fabricated business paper explicitly marked."""
    from fastapi import FastAPI
    from pydantic import ValidationError
    from acps_sdk.aip import Product, StructuredDataItem, TaskState, TextDataItem
    from acps_sdk.aip.aip_rpc_server import (
        CommandHandlers, DefaultHandlers, TaskManager, add_aip_rpc_router,
    )
    from aip.wu_delivery.contracts import ResearchTask, ResearchResult

    app = FastAPI(title="ResearchMesh Literature OFFLINE fixture AIP")

    async def start(command, existing):
        if existing is not None:
            return existing
        structured = [x for x in (command.dataItems or []) if isinstance(x, StructuredDataItem)]
        try:
            if len(structured) != 1 or len(command.dataItems or []) != 1:
                raise ValueError("ONE_TASK_REQUIRED")
            wire = ResearchTask.model_validate(structured[0].data)
            if wire.taskType != "literature_search":
                raise ValueError("ONLY_LITERATURE_SUPPORTED")
        except (ValueError, ValidationError, TypeError):
            rejected = TaskManager.create_task(
                command, initial_state=TaskState.Rejected,
                data_items=[TextDataItem(text="INVALID_FIXTURE_LITERATURE_TASK")],
            )
            rejected.senderId = "fixture-literature-partner"
            return rejected
        response = ResearchResult.model_validate(offline_paper_record(wire.subtaskId))
        task = TaskManager.create_task(command, initial_state=TaskState.Working)
        task.senderId = "fixture-literature-partner"
        TaskManager.set_products(task.taskId, [Product(
            id=f"fixture-literature-{task.taskId}", name="offline-paper-fixture",
            dataItems=[StructuredDataItem(data=response.model_dump(mode="json"))],
        )])
        TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
        return TaskManager.get_task(task.taskId)

    add_aip_rpc_router(app, "/rpc", CommandHandlers(
        on_start=start, on_get=DefaultHandlers.get,
        on_complete=DefaultHandlers.complete, on_cancel=DefaultHandlers.cancel,
        on_continue=DefaultHandlers.continue_,
    ), identity_binding_enabled=False)
    return app


def _base_outcome(outcome: Any, *, fixture: bool) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "protocolCompleted": outcome.protocol_completed,
        "businessValidated": outcome.business_validated,
        "researchSucceeded": outcome.research_succeeded,
        "isFixture": fixture,
        "errorCode": outcome.error_code,
        "result": outcome.result,
        "taskId": outcome.task_id,
        "sessionId": outcome.session_id,
    }


async def invoke_local_agent(bound: dict[str, Any], fixture_dir: Path) -> dict[str, Any]:
    """Invoke a *separate* official SDK in-memory RPC for each task."""
    from aip.wu_delivery.structured import ResearchClient
    kind = bound["taskType"]
    # DependsOn was satisfied by dispatch_plan. Only seven fields cross AIP.
    task = deepcopy(bound)
    task["dependsOn"] = []
    if kind == "literature_search":
        from uuid import uuid4
        from aip.wu_delivery.adapter import invoke_for_planner
        from aip.wu_delivery.contracts import ResearchTask
        from common.models import BUSINESS_TASK_FIELDS
        payload = {key: deepcopy(task[key]) for key in BUSINESS_TASK_FIELDS}
        payload["schemaVersion"] = "0.1"  # Wu frozen business envelope only.
        wire = ResearchTask.model_validate(payload)
        app = create_literature_fixture_app()
        client = ResearchClient(
            partner_url="http://127.0.0.1:8811/rpc", leader_id="three-agent-fixture-leader",
            identity_binding_enabled=False, transport=httpx.ASGITransport(app=app),
        )
        try:
            raw = await invoke_for_planner(
                client, wire, session_id=str(uuid4()), task_id=str(uuid4()),
                timeout_seconds=20.0,
            )
        finally:
            await client.close()
        output = raw.to_dict()
        v01 = output.get("result")
        normalized = deepcopy(v01) if isinstance(v01, dict) else None
        validated = False
        if normalized is not None:
            if normalized.get("schemaVersion") != "0.1":
                raise ValueError("INVALID_LITERATURE_WIRE_VERSION")
            normalized["schemaVersion"] = "0.2"
            validate_result(normalized, bound)
            validated = True
        return {
            "status": output["status"], "protocolCompleted": output["protocolCompleted"],
            "businessValidated": validated and output["businessReady"],
            "researchSucceeded": False, "isFixture": True,
            "errorCode": "LITERATURE_AIP_FAILED" if output.get("error") else None,
            "result": normalized, "taskId": output["taskId"],
            "sessionId": output["sessionId"],
        }

    if kind == "paper_read":
        from agents.paper_reader.researchmesh_paper_reader.agent import PaperReaderAgent
        from aip.paper_reader_v02_bridge import (
            business_task_from_planner, create_reader_local_app, invoke_reader_aip,
        )
        request = business_task_from_planner(task)
        if any(ref.pdfUrl is not None for ref in request.input.papers):
            raise ValueError("OFFLINE_READER_CANNOT_USE_PDF")

        class NoNetworkProvider:
            async def analyze(self, paper):
                raise AssertionError("OFFLINE_READER_NETWORK_FORBIDDEN")

        app = create_reader_local_app(
            PaperReaderAgent(provider=NoNetworkProvider()), backend_timeout_seconds=20.0,
        )
        client = ResearchClient(
            partner_url="http://127.0.0.1:8812/rpc", leader_id="three-agent-fixture-leader",
            identity_binding_enabled=False, transport=httpx.ASGITransport(app=app),
        )
        try:
            outcome = await invoke_reader_aip(
                client, request, timeout_seconds=30.0, backend_mode="fixture",
            )
        finally:
            await client.close()
        return _base_outcome(outcome, fixture=True)

    if kind == "repository_analyze":
        from agents.repository.researchmesh_repository import RepositoryAgent
        from agents.repository.tests.test_repository_agent import FakeFetcher, FakeStaticProvider
        from aip.repository_v02_bridge import (
            business_task_from_planner, create_repository_local_app, invoke_repository_aip,
        )
        # Leader input may include papers/paperNotes; Zhang's static-only Partner
        # accepts repositoryUrls ONLY. Already validated by bind_task_input.
        repo_task = deepcopy(task)
        repo_task["input"] = {"repositoryUrls": task["input"].get("repositoryUrls", [])}
        repo_task["constraints"] = {
            "maxRepositories": task["constraints"].get("maxRepositories", 1),
            "executeCode": task["constraints"].get("executeCode", False),
            "allowRepositorySearch": task["constraints"].get("allowRepositorySearch", False),
        }
        request = business_task_from_planner(repo_task)
        (fixture_dir / "README.md").write_text("# Offline Repository fixture\n", encoding="utf-8")
        (fixture_dir / "pyproject.toml").write_text(
            "[project]\nname = 'offline-test'\n", encoding="utf-8"
        )
        agent = RepositoryAgent(
            fetcher=FakeFetcher(fixture_dir), codewiki_provider=FakeStaticProvider(),
            enrichment_enabled=False,
        )
        app = create_repository_local_app(agent, backend_mode="fixture")
        client = ResearchClient(
            partner_url="http://127.0.0.1:8813/rpc", leader_id="three-agent-fixture-leader",
            identity_binding_enabled=False, transport=httpx.ASGITransport(app=app),
        )
        try:
            outcome = await invoke_repository_aip(
                client, request, timeout_seconds=30.0, backend_mode="fixture",
            )
        finally:
            await client.close()
        return _base_outcome(outcome, fixture=True)

    raise ValueError("UNKNOWN_TASK_TYPE")


async def run_fixture_plan(plan: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="researchmesh-three-aip-") as folder:
        async def invoke(task: dict[str, Any]) -> dict[str, Any]:
            return await invoke_local_agent(task, Path(folder))
        return await dispatch_plan(plan, invoke, timeout_seconds=90.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
        record = asyncio.run(run_fixture_plan(plan))
    except Exception as exc:
        # Error class only, because raw provider/SKD text may contain credentials.
        print("THREE_AGENT_FIXTURE_FAILED:", type(exc).__name__)
        return 1

    path = ROOT / "outputs" / (
        "three-agent-aip-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    path.mkdir(parents=True, exist_ok=False)
    (path / "report.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("mode: OFFLINE_THREE_AGENT_AIP_WITH_PARTIAL_BUSINESS_RESULTS")
    for rec in record["records"]:
        print(rec["subtaskId"], rec["taskType"], rec["status"],
              "protocolCompleted=", rec["protocolCompleted"],
              "dataUsable=", rec["dependencyUsable"],
              "researchSucceeded=", rec["researchSucceeded"],
              "errorCode=", rec["errorCode"])
    print("allProtocolCompleted:", record["allProtocolCompleted"])
    print("pipelineResearchSucceeded:", record["pipelineResearchSucceeded"])
    print("platformConnected:", record["platformConnected"])
    print("Evidence:", path)
    # Success of the LOCAL handshake, NOT research: we expect S2 no-fulltext.
    expected = {r["subtaskId"]: r for r in record["records"]}
    ok = (record["allProtocolCompleted"]
          and set(expected) == {"S1", "S2", "S3"}
          and expected["S1"]["dependencyUsable"]
          and not expected["S2"]["dependencyUsable"]
          and expected["S3"]["dependencyUsable"]
          and not record["pipelineResearchSucceeded"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
