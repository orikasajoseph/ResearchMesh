"""从唯一模型源导出结构 Schema；跨任务关系仍需 Plan 的校验器检查。"""

import json
from pathlib import Path

from common.models import BusinessTask, Plan, PlannerTask, ResearchResult


SCHEMA_MODELS = {
    "task.schema.json": PlannerTask,
    "business_task.schema.json": BusinessTask,
    "plan.schema.json": Plan,
    "result.schema.json": ResearchResult,
}


def build_schemas():
    return {
        name: {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
        for name, model in SCHEMA_MODELS.items()
    }


def export_schemas(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in build_schemas().items():
        (directory / name).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    destination = Path(__file__).resolve().parent / "schemas"
    export_schemas(destination)
    print(f"已导出 {len(SCHEMA_MODELS)} 个 Schema：{destination}")
