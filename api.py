"""仅提供预写样例的本地 Planner API；不接模型、科研工具或 AIP。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Literal

from fastapi import FastAPI, HTTPException

from common.models import ContractModel, Plan, UserQuery
from demo_planner import ExampleModel
from planner import Planner, PlannerError


class PlanRequest(ContractModel):
    userQuery: UserQuery


class PlanResponse(ContractModel):
    demoOnly: Literal[True] = True
    plannerMode: Literal["fixture"] = "fixture"
    plan: Plan


class HealthResponse(ContractModel):
    service: str = "ResearchMesh Planner"
    healthy: bool = True
    demoOnly: Literal[True] = True
    plannerMode: Literal["fixture"] = "fixture"
    realModelConnected: Literal[False] = False
    realAgentsConnected: Literal[False] = False
    aipConnected: Literal[False] = False


class DemoCase(ContractModel):
    name: str
    userQuery: UserQuery


CASES = json.loads(
    (Path(__file__).parent / "examples" / "planner_cases.json").read_text(encoding="utf-8")
)

app = FastAPI(
    title="ResearchMesh 本地 Planner（模拟模式）",
    version="0.1",
    description=(
        "仅接受 /demo/cases 中的预写问题。此服务不是科研 Partner，也不是 AIP 接口；"
        "没有连接真实模型或真实 Agent。/plan 只规划，不执行科研任务。"
    ),
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/demo/cases", response_model=List[DemoCase])
async def demo_cases() -> List[DemoCase]:
    return [DemoCase(name=case["name"], userQuery=case["plan"]["userQuery"]) for case in CASES]


@app.post("/plan", response_model=PlanResponse)
async def plan(request: PlanRequest) -> PlanResponse:
    selected = next((case for case in CASES if case["plan"]["userQuery"] == request.userQuery), None)
    if selected is None:
        raise HTTPException(status_code=409, detail={
            "code": "MOCK_QUERY_NOT_SUPPORTED",
            "message": "当前只支持预写样例，请从 GET /demo/cases 复制 userQuery；任意新问题需接入真实模型。",
        })
    try:
        result = await Planner(ExampleModel(selected["plan"])).plan(request.userQuery)
    except PlannerError as exc:
        raise HTTPException(status_code=500, detail={
            "code": "PLANNER_FAILED", "message": "样例计划生成失败，请查看本地测试。",
        }) from exc
    return PlanResponse(plan=Plan.model_validate(result))
