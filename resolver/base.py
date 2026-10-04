"""本地接口，不是网络协议；真实发现与 AIP 适配器以后实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol


class AgentExecutor(Protocol):
    async def execute(
        self,
        payload: dict[str, Any],
        dependency_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """执行业务任务。dependency_results 是 Leader 内部上下文，不是 AIP 字段。"""


@dataclass(frozen=True)
class AgentHandle:
    agent_id: str
    capabilities: tuple[str, ...]
    executor: AgentExecutor


class NoMatchingAgentError(LookupError):
    pass


class AgentResolver(ABC):
    @abstractmethod
    async def resolve(self, required_capability: str) -> AgentHandle:
        """按能力找执行者。Planner 不应知道执行者的地址或身份。"""
