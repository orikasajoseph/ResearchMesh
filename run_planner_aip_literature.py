r"""Run one Planner v0.2 Literature task through the existing local AIP Partner.

No LLM, ACS, ADP, MQ, public server, or registry calls. The default mode
validates and translates the saved plan without using Semantic Scholar.
Pass --live explicitly to perform ONE real Literature lookup over an in-memory
ASGI transport using the official ACPS SDK v2.2.0 already in the project .venv.

From ResearchMesh repository root:
    .\.venv\Scripts\python.exe -X utf8 run_planner_aip_literature.py
    .\.venv\Scripts\python.exe -X utf8 run_planner_aip_literature.py --live
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from aip.literature_bridge import execute_literature
from aip.wu_delivery.adapter import invoke_for_planner
from aip.wu_delivery.contracts import ResearchTask
from aip.wu_delivery.partner import create_local_app
from aip.wu_delivery.structured import ResearchClient
from planner_contract import validate_plan
from result_contract import validate_result


ROOT = Path(__file__).resolve().parent
DEFAULT_PLAN = ROOT / "examples" / "planner_aip_literature_live_v02.json"
# Explicit AIP business fields: never pass requiredSkill or dependsOn to Partner.
BUSINESS_FIELDS = (
    "schemaVersion", "subtaskId", "taskType", "goal",
    "input", "constraints", "outputSchema",
)


def read_and_convert_plan(plan_path: Path) -> tuple[dict[str, Any], dict[str, Any], ResearchTask]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schemaVersion") != "0.2":
        raise ValueError("PLAN_VERSION_MUST_BE_0_2")
    validate_plan(plan)
    if plan.get("needsClarification"):
        raise ValueError("PLAN_NEEDS_CLARIFICATION")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1:
        raise ValueError("SINGLE_LITERATURE_TASK_REQUIRED")
    task = tasks[0]
    if task.get("taskType") != "literature_search" or task.get("requiredSkill") != "literature.search":
        raise ValueError("LITERATURE_SKILL_REQUIRED")
    if task.get("dependsOn") != []:
        raise ValueError("UNEXECUTED_DEPENDENCIES_NOT_SUPPORTED")
    payload = {key: deepcopy(task[key]) for key in BUSINESS_FIELDS}
    payload["schemaVersion"] = "0.1"  # Frozen Wu AIP business envelope.
    wire_task = ResearchTask.model_validate(payload)
    return plan, task, wire_task


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def execute_live(wire_task: ResearchTask) -> dict[str, Any]:
    # No uvicorn port is opened. The official SDK talks to a local ASGI Partner.
    partner = create_local_app(execute=execute_literature, backend_timeout_seconds=90.0)
    client = ResearchClient(
        partner_url="http://127.0.0.1:8011/rpc",
        leader_id="planner-v02-local-leader",
        identity_binding_enabled=False,
        transport=httpx.ASGITransport(app=partner),
    )
    try:
        outcome = await invoke_for_planner(
            client, wire_task,
            session_id=str(uuid4()), task_id=str(uuid4()),
            timeout_seconds=100.0,
        )
        data = outcome.to_dict()
        if data.get("error"):
            # Do not write provider exception details that might contain sensitive inputs.
            data["error"] = data["error"].split(":", 1)[0]
        return data
    finally:
        await client.close()


def evaluate(outcome: dict[str, Any], task_v02: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "protocolCompleted": outcome.get("protocolCompleted") is True,
        "businessReady": outcome.get("businessReady") is True,
        "statusCompleted": outcome.get("status") == "completed",
        "transportTestOnly": False,
        "resultV02Validated": False,
        "paperCount": 0,
        "researchSucceeded": False,
    }
    result_v01 = outcome.get("result")
    warnings = list(outcome.get("warnings") or [])
    result_warnings = (result_v01 or {}).get("warnings") or []
    report["transportTestOnly"] = any(
        str(w).startswith("TRANSPORT_TEST_ONLY") for w in [*warnings, *result_warnings]
    )
    if not isinstance(result_v01, dict):
        report["reason"] = outcome.get("error") or "NO_RESEARCH_RESULT"
        return report

    if result_v01.get("schemaVersion") != "0.1":
        report["reason"] = "WIRE_RESULT_NOT_V0_1"
        return report

    # Only the explicit envelope version is translated. Do not change items,
    # kinds, task correlation, evidence or other business content.
    result_v02 = deepcopy(result_v01)
    result_v02["schemaVersion"] = "0.2"
    try:
        validate_result(result_v02, task_v02)
    except (ValueError, TypeError, KeyError) as exc:
        report["reason"] = "RESULT_V02_VALIDATION_FAILED"
        report["validationErrorType"] = type(exc).__name__
        return report

    report["resultV02Validated"] = True
    report["paperCount"] = len(result_v02.get("items", []))
    report["researchSucceeded"] = all([
        report["protocolCompleted"], report["businessReady"],
        report["statusCompleted"], not report["transportTestOnly"],
        outcome.get("error") is None, report["paperCount"] > 0,
    ])
    if not report["researchSucceeded"]:
        report["reason"] = "NO_PAPERS_OR_PROTOCOL_NOT_SUCCESSFUL"
    report["normalizedResult"] = result_v02
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="One-task local Planner v0.2 -> AIP -> Literature integration")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="Saved full Planner v0.2 JSON plan")
    parser.add_argument("--live", action="store_true", help="Actually call Semantic Scholar via Literature Agent")
    args = parser.parse_args()
    try:
        _, task_v02, wire_task = read_and_convert_plan(args.plan)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Plan validation/translation failed: {type(exc).__name__}: {exc}")
        return 2

    print("Plan v0.2: valid")
    print("Selected:", task_v02["subtaskId"], task_v02["taskType"], task_v02["requiredSkill"])
    print("AIP business wire:", wire_task.schemaVersion)
    if not args.live:
        print("DRY RUN OK: No model, network API, or real research was invoked.")
        return 0

    load_dotenv(ROOT / ".env")
    if not os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
        print("SEMANTIC_SCHOLAR_API_KEY missing from project root .env; no API call made.")
        return 2

    out_dir = ROOT / "outputs" / ("planner-aip-literature-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=False)
    # Redact secrets by never serializing environment variables or provider errors.
    save_json(out_dir / "task_v02.json", task_v02)
    save_json(out_dir / "wire_task_v01.json", wire_task.model_dump(mode="json"))
    try:
        outcome = asyncio.run(execute_live(wire_task))
    except Exception as exc:
        save_json(out_dir / "failure.json", {"errorType": type(exc).__name__, "success": False})
        print(f"AIP local invocation failed: {type(exc).__name__}; evidence: {out_dir}")
        return 1
    save_json(out_dir / "aip_outcome.json", outcome)
    assessment = evaluate(outcome, task_v02)
    result_v02 = assessment.pop("normalizedResult", None)
    if result_v02 is not None:
        save_json(out_dir / "result_v02.json", result_v02)
    save_json(out_dir / "assessment.json", assessment)

    print("AIP status:", outcome.get("status"))
    print("protocolCompleted:", assessment["protocolCompleted"])
    print("businessReady:", assessment["businessReady"])
    print("resultV02Validated:", assessment["resultV02Validated"])
    print("papers:", assessment["paperCount"])
    print("researchSucceeded:", assessment["researchSucceeded"])
    if not assessment["researchSucceeded"]:
        print("reason:", assessment.get("reason", outcome.get("error", "UNKNOWN")))
    print("Evidence:", out_dir)
    return 0 if assessment["researchSucceeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
