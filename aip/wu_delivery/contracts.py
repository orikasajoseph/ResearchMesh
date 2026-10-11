"""ResearchMesh business payloads; these are not another wire protocol."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

TASK_SCHEMAS = {
    "literature_search": "paper_records.v1",
    "paper_read": "paper_analysis.v1",
    "code_search": "code_records.v1",
}


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal["0.1"] = "0.1"
    subtaskId: str = Field(min_length=1)
    taskType: Literal["literature_search", "paper_read", "code_search"]
    goal: str = Field(min_length=1)
    input: dict
    constraints: dict = Field(default_factory=dict)
    outputSchema: str

    @model_validator(mode="after")
    def check_business_input(self):
        if self.outputSchema != TASK_SCHEMAS[self.taskType]:
            raise ValueError("outputSchema does not match taskType")
        # Validate the minimum input required by each v0.1 task type.
        required = "paperUrl" if self.taskType == "paper_read" else "query"
        if not isinstance(self.input.get(required), str) or not self.input[required].strip():
            raise ValueError(f"input.{required} must be nonempty text")
        return self


class ResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["paper", "paper_analysis", "code"]
    data: dict


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal["0.1"] = "0.1"
    subtaskId: str = Field(min_length=1)
    taskType: Literal["literature_search", "paper_read", "code_search"]
    summary: str
    items: list[ResultItem]
    warnings: list[str] = Field(default_factory=list)


def transport_only_result(task: ResearchTask) -> ResearchResult:
    return ResearchResult(
        subtaskId=task.subtaskId, taskType=task.taskType,
        summary="Structured task received and validated; research backend not connected.",
        items=[], warnings=["TRANSPORT_TEST_ONLY: no research was performed"],
    )
