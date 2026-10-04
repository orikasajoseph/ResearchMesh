"""真实规划 HTTP 入口。不是 Partner/AIP 服务；不替换原 api.py 的 Mock 接口。"""

from fastapi import FastAPI, HTTPException
from api import PlanRequest
from common.models import Plan
from run_planner import create_plan


def create_app(planning_function=create_plan):
    app = FastAPI(title="ResearchMesh 真实 Planner（调用会消耗额度）", version="0.1")

    @app.get("/health")
    async def health():
        # 健康检查不调用付费 API，也不能证明网络/Key 可用。
        return {"healthy": True, "plannerMode": "live", "modelConnectionChecked": False,
                "agentsExecuted": False, "aipUsed": False}

    @app.post("/plan")
    async def plan(request: PlanRequest):
        try:
            value, metadata = await planning_function(request.userQuery)
            checked = Plan.model_validate(value)
            return {"plan": checked.model_dump(), "metadata": metadata}
        except Exception:
            raise HTTPException(502, "真实规划失败；请在本机检查配置、额度或输入。") from None

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    # 此内部接口没有远程鉴权，仅绑定回环地址，禁止直接发布到公网。
    uvicorn.run(app, host="127.0.0.1", port=9041)
