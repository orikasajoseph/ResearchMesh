"""Local test registry for AIP handles; not platform ADP discovery."""
from __future__ import annotations

from aip_executor import AipAgentExecutor
from aip_lifecycle import AipTaskRunner
from resolver.base import AgentHandle, AgentResolver, NoMatchingAgentError


class LocalAipResolver(AgentResolver):
    def __init__(self, registrations, on_event=None):
        self._registrations = dict(registrations)
        self._on_event = on_event

    async def resolve(self, required_capability):
        if self._on_event:
            self._on_event("LOCAL_RESOLVE_STARTED", skill=required_capability)
        registration = self._registrations.get(required_capability)
        if registration is None:
            raise NoMatchingAgentError(required_capability)
        partner_aic, send, timeout_seconds, poll_seconds = registration
        if self._on_event:
            self._on_event("LOCAL_RESOLVE_SUCCESS", skill=required_capability, agent=partner_aic)
        runner = AipTaskRunner(send, "local-repository-leader", partner_aic,
                               timeout_seconds=timeout_seconds, poll_seconds=poll_seconds)
        return AgentHandle(partner_aic, (required_capability,), AipAgentExecutor(runner))
