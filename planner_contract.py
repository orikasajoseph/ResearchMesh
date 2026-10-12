"""保留旧调用入口：Pydantic Plan 结构校验及执行约束白名单。"""

from typing import Any

from common.models import Plan, TASK_FIELDS, TASK_TYPES


# 与现有 Wrapper/Leader Adapter 接受的约束一致，不新增业务字段。
ALLOWED_CONSTRAINTS = {
    "literature_search": frozenset({"yearFrom", "yearTo", "maxResults"}),
    "paper_read": frozenset({"maxPapers"}),
    "repository_analyze": frozenset({"maxRepositories", "executeCode", "allowRepositorySearch", "requirePaperNotes"}),
}


class UnsupportedConstraintError(ValueError):
    code = "UNSUPPORTED_CONSTRAINT"


def validate_plan(plan: dict[str, Any]) -> None:
    """结构和受支持约束全部通过才接受；失败抛出 ValueError 子类。"""
    validated = Plan.model_validate(plan)
    for task in validated.tasks:
        allowed = ALLOWED_CONSTRAINTS[task.taskType]
        if set(task.constraints) - allowed:
            raise UnsupportedConstraintError(
                f"{task.subtaskId} 的 {task.taskType} constraints 只能使用："
                + ", ".join(sorted(allowed))
                + "；保留证据、分析重点等要求应写入 goal，不能新增约束字段。"
            )
