"""无需 API Key 的链路演示；这里的模型响应来自固定样例。"""

import asyncio
import json
from pathlib import Path

from planner import Planner


class ExampleModel:
    def __init__(self, plan: dict):
        self.plan = plan

    async def generate(self, instructions: str, user_query: str) -> str:
        return json.dumps(self.plan, ensure_ascii=False)


async def main() -> None:
    path = Path(__file__).parent / "examples" / "planner_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    example = cases[0]["plan"]
    plan = await Planner(ExampleModel(example)).plan(example["userQuery"])
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
