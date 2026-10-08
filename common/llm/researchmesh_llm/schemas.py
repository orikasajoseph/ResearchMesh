from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchMeshLLMModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ChatMessage(ResearchMeshLLMModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class LLMUsage(ResearchMeshLLMModel):
    promptTokens: int | None = Field(default=None, ge=0)
    completionTokens: int | None = Field(default=None, ge=0)
    totalTokens: int | None = Field(default=None, ge=0)


class LLMResponse(ResearchMeshLLMModel):
    provider: Literal["deepseek"] = "deepseek"
    model: str
    content: str
    latencyMs: float = Field(ge=0)
    usage: LLMUsage | None = None
    requestId: str | None = None
    warnings: list[str] = Field(default_factory=list)
