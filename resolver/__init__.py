"""Leader 内部的能力解析接口；当前只有本地 Mock 实现。"""

from .base import AgentExecutor, AgentHandle, AgentResolver, NoMatchingAgentError
from .mock import MockAgentResolver

__all__ = ["AgentExecutor", "AgentHandle", "AgentResolver", "NoMatchingAgentError", "MockAgentResolver"]
