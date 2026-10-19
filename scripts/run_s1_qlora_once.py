"""One real QLoRA S1 search through the existing local official AIP stack.

No Planner model, Reader, Repository, or DeepSeek call. The Partner has both
outer retry disabled and a one-request Semantic Scholar HTTP send budget.
"""
from __future__ import annotations

import asyncio
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from acps_sdk.aip import AipRpcClient
import httpx

from aip_lifecycle import sdk_sender
from aip_orchestrator import AipOrchestrator
from planner import Planner
from resolver.local_aip import LocalAipResolver
from run_aip_literature import live_partner
from run_aip_repository import save_json
from run_literature import prepare_live_wrapper


ROOT = Path(__file__).resolve().parents[1]
TITLE = "QLoRA: Efficient Finetuning of Quantized LLMs"
ARXIV = "2305.14314"
REPOSITORY = "https://github.com/artidoro/qlora"


class FixedS1PlanModel:
    async def generate(self, instructions, user_query):
        return json.dumps({"schemaVersion": "0.2", "userQuery": user_query,
            "needsClarification": False, "clarificationQuestion": None,
            "tasks": [{"schemaVersion": "0.2", "subtaskId": "S1",
                       "taskType": "literature_search", "goal": "Find the QLoRA paper and verified resources",
                       "input": {"query": TITLE}, "constraints": {"maxResults": 1},
                       "outputSchema": "paper_records.v1", "requiredSkill": "literature.search",
                       "dependsOn": []}]})


async def run(*, preflight=False):
    os.environ["RESEARCHMESH_S1_SINGLE_REQUEST"] = "1"
    wrapper, config = prepare_live_wrapper(ROOT / "agents/literature")
    provider = wrapper.SemanticScholarProvider(config=config)
    requester = provider.client._requester
    if config.max_retries != 0 or provider.client._retry is not False or requester.max_requests != 1:
        raise RuntimeError("SINGLE_REQUEST_GUARD_NOT_READY")
    query = "Find QLoRA by its exact title, then report only verified S1 resources"
    plan = await Planner(FixedS1PlanModel(), max_attempts=1).plan(query)
    if len(plan["tasks"]) != 1 or plan["tasks"][0]["constraints"]["maxResults"] != 1:
        raise RuntimeError("SINGLE_S1_PLAN_REQUIRED")
    if preflight:
        print(json.dumps({"preflight": "passed", "apiKeyConfigured": bool(config.api_key),
                          "sdkRetry": provider.client._retry, "providerRetries": config.max_retries,
                          "semanticScholarRequestBudget": requester.max_requests,
                          "resourceResolverEnabled": config.resolve_resources,
                          "networkAttempted": False, "llmCalled": False}))
        return 0

    output = ROOT / "outputs" / datetime.now().strftime("aip-s1-qlora-once-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "plan.json", plan)
    wire = []
    started = perf_counter()
    with live_partner(ROOT / "agents/literature", output,
                      extra_env={"RESEARCHMESH_S1_SINGLE_REQUEST": "1"}) as (url, aic):
        class TracedClient(AipRpcClient):
            async def _send_request(self, command):
                response = await super()._send_request(command)
                wire.append({"command": command.model_dump(mode="json", exclude_none=True),
                             "response": response.model_dump(mode="json", exclude_none=True)})
                return response
        client = TracedClient(url, "local-s1-once-leader", identity_binding_enabled=False,
                              transport=httpx.AsyncHTTPTransport(trust_env=False))
        try:
            resolver = LocalAipResolver({"literature.search": (aic, sdk_sender(client), 135, .5)})
            leader = await AipOrchestrator(resolver, task_timeout_seconds=140).run_plan(plan)
        finally:
            await client.close()
    save_json(output / "leader_result.json", leader)
    save_json(output / "aip_messages.json", wire)
    backend_path = output / "backend_outcome.json"
    backend = json.loads(backend_path.read_text(encoding="utf-8")) if backend_path.is_file() else {}
    diagnostics = backend.get("diagnostics") or {}
    requests = diagnostics.get("httpRequests") or []
    record = leader["executionRecords"][0]
    paper = (leader["results"][0]["items"][0]["data"]
             if record["succeeded"] and leader["results"][0]["items"] else {})
    normalized_title = " ".join((paper.get("title") or "").casefold().split())
    identity = any(ARXIV in str(value) for value in
                   [paper.get("doi"), paper.get("url"), paper.get("pdfUrl"),
                    *(paper.get("sourceUrls") or [])])
    resource = (diagnostics.get("resourceResolution") or {}).get("papers") or []
    acceptance = {"durationSeconds": round(perf_counter() - started, 3),
                  "semanticScholarHttpRequests": len(requests),
                  "semanticScholarHttpStatus": requests[0].get("httpStatus") if requests else None,
                  "apiKeyConfigured": requests[0].get("apiKeyConfigured") if requests else None,
                  "apiKeyAttached": requests[0].get("apiKeyAttached") if requests else None,
                  "retryAfter": requests[0].get("retryAfterHeader") if requests else None,
                  "rateLimitHeaders": requests[0].get("rateLimitHeaders") if requests else {},
                  "responseReason": requests[0].get("responseReason") if requests else None,
                  "searchCacheHit": (diagnostics.get("metrics") or {}).get("cacheHit"),
                  "wrapperAttempts": (diagnostics.get("metrics") or {}).get("attempts"),
                  "aipCompleted": record["aipState"] == "completed",
                  "leaderValidated": record["succeeded"],
                  "paperRecordsV1": bool(paper and plan["tasks"][0]["outputSchema"] == "paper_records.v1"),
                  "qloraTitleMatched": normalized_title == TITLE.casefold(),
                  "arxivIdentityMatched": identity,
                  "pdfUrl": paper.get("pdfUrl"), "repositoryUrls": paper.get("repositoryUrls", []),
                  "resourceEvidence": resource,
                  "aipCommands": [event["command"]["command"] for event in wire],
                  "backendErrorCode": (backend.get("error") or {}).get("code"),
                  "leaderErrorCode": (record.get("error") or {}).get("code"),
                  "s1Only": len(plan["tasks"]) == 1,
                  "llmCalled": False}
    if len(requests) > 1:
        raise RuntimeError("SEMANTIC_SCHOLAR_REQUEST_BUDGET_VIOLATED")
    save_json(output / "acceptance.json", acceptance)
    print(json.dumps({"recordDir": str(output), "acceptance": acceptance}, ensure_ascii=False))
    return 0 if acceptance["aipCompleted"] and acceptance["qloraTitleMatched"] and identity else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    raise SystemExit(asyncio.run(run(preflight=parser.parse_args().preflight)))
