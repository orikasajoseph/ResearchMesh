"""共享 v0.1 业务外壳和 Leader 内部计划，使用严格类型校验。"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskType = Literal["literature_search", "paper_read", "code_search"]
TASK_TYPES = set(get_args(TaskType))
NonEmptyString = Annotated[str, Field(min_length=1, pattern=r"\S")]
UserQuery = Annotated[str, Field(min_length=1, max_length=6000, pattern=r"\S")]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BusinessTask(ContractModel):
    """可放入结构化数据项的业务内容；本身不是 TaskCommand。"""
    schemaVersion: Literal["0.1"]
    subtaskId: NonEmptyString
    taskType: TaskType
    goal: NonEmptyString
    input: Dict[str, Any]
    constraints: Dict[str, Any]
    outputSchema: NonEmptyString


class PlannerTask(BusinessTask):
    """Leader 内部任务，额外包含发现和依赖信息。"""
    requiredCapability: NonEmptyString
    dependsOn: List[NonEmptyString]


PLAN_CONDITIONS = {
    "allOf": [{
        "if": {"properties": {"needsClarification": {"const": True}}, "required": ["needsClarification"]},
        "then": {"properties": {"tasks": {"maxItems": 0}, "clarificationQuestion": {"type": "string", "minLength": 1, "pattern": r"\S"}}},
        "else": {"properties": {"tasks": {"minItems": 1}, "clarificationQuestion": {"type": "null"}}},
    }]
}


class Plan(ContractModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=PLAN_CONDITIONS)
    schemaVersion: Literal["0.1"]
    userQuery: UserQuery
    needsClarification: bool
    clarificationQuestion: Optional[NonEmptyString] = Field(...)
    tasks: List[PlannerTask]

    @model_validator(mode="after")
    def check_plan_relationships(self):
        from common.skills import SKILLS, OUTPUT_SCHEMAS
        from input_binding import validate_task_input
        if self.needsClarification:
            if self.tasks or not self.clarificationQuestion:
                raise ValueError("需要澄清时必须给出追问，且 tasks 为空")
            return self
        if not self.tasks or self.clarificationQuestion is not None:
            raise ValueError("无需澄清时至少有一个任务，且追问为 null")
        seen = set()
        for task in self.tasks:
            if task.requiredCapability != SKILLS[task.taskType]:
                raise ValueError("requiredCapability 必须是任务对应的业务 skill 点分名称")
            if task.outputSchema != OUTPUT_SCHEMAS[task.taskType]:
                raise ValueError("outputSchema 与 taskType 不匹配")
            validate_task_input(task.model_dump())
            if task.subtaskId in seen:
                raise ValueError("subtaskId 不能重复")
            if len(task.dependsOn) != len(set(task.dependsOn)):
                raise ValueError("dependsOn 不能重复")
            if any(dependency not in seen for dependency in task.dependsOn):
                raise ValueError("dependsOn 只能引用前面已定义的任务，不能自依赖或循环")
            if task.taskType == "code_search" and task.constraints.get("requirePaperNotes"):
                if not task.input.get("paperNotes") and not any(
                    previous.subtaskId in task.dependsOn and previous.taskType == "paper_read"
                    for previous in self.tasks
                ):
                    raise ValueError("需要论文阅读结果的代码分析必须直接依赖 paper_read")
            seen.add(task.subtaskId)
        return self


class ResultItem(ContractModel):
    kind: NonEmptyString
    data: Dict[str, Any]


class ResearchResult(ContractModel):
    """业务结果，状态仍由未来真实 AIP TaskResult 承载。"""
    schemaVersion: Literal["0.1"]
    subtaskId: NonEmptyString
    taskType: TaskType
    summary: NonEmptyString
    items: List[ResultItem]
    warnings: List[str]


BUSINESS_TASK_FIELDS = tuple(BusinessTask.model_fields)
TASK_FIELDS = tuple(PlannerTask.model_fields)
RESULT_FIELDS = set(ResearchResult.model_fields)
