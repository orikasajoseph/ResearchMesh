"""Leader capability resolution interfaces and local fixtures.

Platform ADP components remain explicit imports so local-only users do not
need the platform SDK just to import ``resolver``.
"""

from .base import AgentExecutor, AgentHandle, AgentResolver, NoMatchingAgentError
from .mock import MockAgentResolver

__all__ = ["AgentExecutor", "AgentHandle", "AgentResolver", "NoMatchingAgentError", "MockAgentResolver"]
