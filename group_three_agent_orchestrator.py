"""Sequential three-Group Leader orchestration with injected stage execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable

from three_agent_dispatch_v02 import dispatch_plan

STAGE_TIMEOUTS = {
    "literature_search": 180.0,
    "paper_read": 690.0,
    "repository_analyze": 210.0,
}
RunStage = Callable[[dict[str, Any], float], Awaitable[dict[str, Any]]]


async def dispatch_group_plan(plan: dict[str, Any], run_stage: RunStage,
                              *, require_formal: bool = True) -> dict[str, Any]:
    """Bind dependencies and run a fresh Group per stage, without retries.

    `run_stage` is the only platform boundary.  Production supplies the formal
    Group Leader function; tests supply a network-free fake.  A failed or
    uncertain stage is not retried and blocks dependent tasks.
    """
    if not callable(run_stage) or type(require_formal) is not bool:
        raise ValueError("INVALID_GROUP_STAGE_RUNNER")
    stage_records = []

    async def invoke(bound):
        task_type = bound["taskType"]
        if task_type not in STAGE_TIMEOUTS:
            raise ValueError("UNSUPPORTED_GROUP_STAGE")
        record = await run_stage(deepcopy(bound), STAGE_TIMEOUTS[task_type])
        if not isinstance(record, dict):
            raise ValueError("INVALID_GROUP_STAGE_RECORD")
        stage_records.append(deepcopy(record))
        result = record.get("result")
        formal = str(record.get("scope", "")).startswith("PLATFORM_")
        passed = record.get("passed") is True
        return {
            "status": "completed" if record.get("protocolCompleted") is True else "failed",
            "protocolCompleted": record.get("protocolCompleted") is True,
            "businessValidated": record.get("resultValidated") is True,
            "isFixture": not formal,
            "researchSucceeded": bool(passed and formal),
            "errorCode": (record.get("error") or {}).get("code"),
            "result": deepcopy(result),
        }

    run = await dispatch_plan(plan, invoke, timeout_seconds=900)
    identifiers = {
        name: [record.get(name) for record in stage_records]
        for name in ("groupId", "sessionId", "taskId", "partnerAic")
    }
    fresh = bool(len(stage_records) == 3 and all(
        all(values) and len(set(values)) == len(values) for values in identifiers.values()
    ))
    formal = bool(stage_records and all(
        str(record.get("scope", "")).startswith("PLATFORM_") for record in stage_records
    ))
    run.update({
        "mode": "formal-group-three-stage" if formal else "offline-group-three-stage",
        "stageRecords": stage_records,
        "freshDistinctStageIdentifiers": fresh,
        "formalEvidenceEligible": formal and fresh,
    })
    if require_formal and not (formal and fresh):
        run["pipelineResearchSucceeded"] = False
    return run
