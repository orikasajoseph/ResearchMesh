"""Explicit local AIP registry; never masquerades as official platform ADP."""
from aip_executor import AipAgentExecutor
from common.skills import SKILLS
from .base import AgentResolver, NoMatchingAgentError


class LocalAipResolver(AgentResolver):
    scope = 'EXPLICIT_LOCAL_AIP_REGISTRY_NOT_PLATFORM_DISCOVERY'

    def __init__(self, agents):
        self.agents = tuple(agents)
        ids = set()
        for handle in self.agents:
            if (not isinstance(handle.agent_id, str) or not handle.agent_id.strip() or handle.agent_id in ids or
                not isinstance(handle.executor, AipAgentExecutor) or
                handle.executor.runner.partner_aic != handle.agent_id or not handle.capabilities or
                any(skill not in SKILLS.values() for skill in handle.capabilities)):
                raise ValueError('INVALID_LOCAL_AIP_REGISTRATION')
            ids.add(handle.agent_id)

    async def resolve(self, required_capability):
        candidates = [a for a in self.agents if required_capability in a.capabilities]
        if len(candidates) != 1:
            raise NoMatchingAgentError('LOCAL_SKILL_MISSING_OR_AMBIGUOUS')
        return candidates[0]
