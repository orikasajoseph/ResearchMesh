"""Strict v0.2 Leader-local, sequential dependency dispatcher.

No SDK, LLM, network, or IO here. `invoke_bound` is a dependency-injected
AIP transport callback returning a structured outcome.  This module does
NOT implement registry discovery, identity binding, or MQ/AMP.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Awaitable, Callable

from common.models import BUSINESS_TASK_FIELDS
from input_binding import bind_task_input
from planner_contract import validate_plan
from result_contract import validate_result

InvokeBound = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def dispatch_plan(
    plan: dict[str, Any], invoke_bound: InvokeBound, *, timeout_seconds: float = 160.0,
) -> dict[str, Any]:
    """Validate, bind, dispatch and preserve each task's independent outcome.

    `dependencyUsable` means structured data can be passed to another *fixture*
    stage; it NEVER implies that real research occurred.
    """
    if not 0 < timeout_seconds <= 3600:
        raise ValueError("INVALID_DISPATCH_TIMEOUT")
    validate_plan(plan)
    run = {
        "mode": "local-aip-v02",
        "platformConnected": False,
        "userQuery": plan["userQuery"],
        "needsClarification": plan["needsClarification"],
        "records": [],
        "results": {},
        "pipelineResearchSucceeded": False,
    }
    if plan["needsClarification"]:
        run["clarificationQuestion"] = plan["clarificationQuestion"]
        return run

    # All tasks have already been validated in topological order by Plan.
    available_results: dict[str, dict[str, Any]] = {}
    dependency_usable: dict[str, bool] = {}
    for task in plan["tasks"]:
        sid = task["subtaskId"]
        record = {
            "subtaskId": sid, "taskType": task["taskType"],
            "dependsOn": list(task["dependsOn"]), "status": "not-started",
            "protocolCompleted": False, "businessValidated": False,
            "dependencyUsable": False, "researchSucceeded": False,
            "isFixture": None, "errorCode": None,
        }
        run["records"].append(record)
        if any(not dependency_usable.get(dep, False) for dep in task["dependsOn"]):
            record.update(status="skipped", errorCode="DEPENDENCY_NOT_USABLE")
            dependency_usable[sid] = False
            continue
        try:
            bound = bind_task_input(task, {
                dep: available_results[dep] for dep in task["dependsOn"]
            })
            # Keep Leader-only skill/dependsOn for any adapter validation, but
            # only dispatch validated seven-field payloads to a Partner.
            record["boundInput"] = deepcopy(bound["input"])
            outcome = await asyncio.wait_for(invoke_bound(deepcopy(bound)), timeout=timeout_seconds)
            if not isinstance(outcome, dict):
                raise ValueError("INVALID_AIP_OUTCOME")
            record["status"] = str(outcome.get("status", "unknown"))
            record["protocolCompleted"] = outcome.get("protocolCompleted") is True
            record["businessValidated"] = outcome.get("businessValidated") is True
            record["isFixture"] = outcome.get("isFixture") is True
            error = outcome.get("errorCode")
            record["errorCode"] = str(error) if error is not None else None
            raw = outcome.get("result")
            if raw is not None:
                # Correlation / evidence / item types, including negative empty cases.
                validate_result(raw, {k: bound[k] for k in BUSINESS_TASK_FIELDS})
                run["results"][sid] = deepcopy(raw)
            if not (record["protocolCompleted"] and record["businessValidated"]
                    and record["status"] == "completed" and record["errorCode"] is None):
                record["errorCode"] = record["errorCode"] or "AIP_NOT_READY"
                dependency_usable[sid] = False
                continue
            usable = bool(raw and raw["items"])
            record["dependencyUsable"] = usable
            dependency_usable[sid] = usable
            if usable:
                available_results[sid] = deepcopy(raw)
            if not usable:
                record["errorCode"] = "NO_USABLE_BUSINESS_ITEMS"
            # A user-visible fixture must never become a real-research success.
            all_real = usable and all(
                item["data"].get("isMock") is not True for item in raw["items"]
            )
            record["researchSucceeded"] = bool(
                usable and all_real and not record["isFixture"]
                and outcome.get("researchSucceeded") is True
            )
            record["warnings"] = list((raw or {}).get("warnings") or [])
        except asyncio.TimeoutError:
            record.update(status="timeout", errorCode="DISPATCH_TIMEOUT")
            dependency_usable[sid] = False
        except Exception:
            # No raw exception string: might include paths, tokens, or URLs.
            record.update(status="failed", errorCode="DISPATCH_OR_CONTRACT_ERROR")
            dependency_usable[sid] = False

    run["pipelineResearchSucceeded"] = bool(run["records"]) and all(
        rec["researchSucceeded"] for rec in run["records"]
    )
    run["allProtocolCompleted"] = all(
        rec["protocolCompleted"] for rec in run["records"]
    )
    run["fixtureOrNegativeRun"] = any(
        rec["isFixture"] or not rec["researchSucceeded"] for rec in run["records"]
    )
    return run
