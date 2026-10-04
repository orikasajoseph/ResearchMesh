"""保留旧调用入口，实际校验统一使用 Pydantic Plan。"""

from typing import Any

from common.models import Plan, TASK_FIELDS, TASK_TYPES


def validate_plan(plan: dict[str, Any]) -> None:
    """成功无返回值，失败抛出 ValueError 的子类 ValidationError。"""
    Plan.model_validate(plan)
