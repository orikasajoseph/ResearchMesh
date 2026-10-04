"""只查本地测试注册表，不声称调用了平台发现服务。"""

from .base import AgentHandle, AgentResolver, NoMatchingAgentError
from common.skills import canonical_skill


class MockAgentResolver(AgentResolver):
    def __init__(self, agents):
        self._agents = list(agents)
        ids = [agent.agent_id for agent in self._agents]
        if len(ids) != len(set(ids)):
            raise ValueError("测试注册表中的 agent_id 不能重复")

    async def resolve(self, required_capability: str) -> AgentHandle:
        for agent in self._agents:
            if required_capability in map(canonical_skill, agent.capabilities):
                return agent
        raise NoMatchingAgentError(f"没有匹配能力 {required_capability} 的模拟执行者")
