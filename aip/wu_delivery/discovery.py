"""SDK 2.2.0 discovery request and candidate adapters.

No platform URL, authentication, or network operation is inferred here.
Candidates are metadata, never evidence of registration or reachability.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Any

from acps_sdk.acs.models import AgentCapabilitySpec
from acps_sdk.adp.models import DiscoveryRequest, DiscoveryResponse
from pydantic import ValidationError

from .contracts import ResearchTask
from .skill_map import required_skill


@dataclass
class DiscoveryOutcome:
    required_skill: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def build_discovery_request(task: ResearchTask, *, limit: int = 5) -> DiscoveryRequest:
    """Keep skill filtering in ADP, without changing the business/AIP contract."""
    skill = required_skill(task)
    return DiscoveryRequest(type="filtered", limit=limit, filter={"conditions": [
        {"field": "active", "op": "eq", "value": True},
        {"field": "skills.id", "op": "eq", "value": skill},
    ]})


def parse_discovery_response(payload: dict[str, Any], task: ResearchTask) -> DiscoveryOutcome:
    """Return ranked ACS candidates, retaining endpoints and security requirements.

    Direct/Group transport choice and authenticated client creation remain separate.
    Malformed ACS entries are excluded individually; malformed envelopes fail closed.
    """
    skill = required_skill(task)
    outcome = DiscoveryOutcome(skill)
    try:
        response = DiscoveryResponse.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        outcome.error = "ADP_INVALID_RESPONSE"
        return outcome
    if response.error is not None:
        # Server messages can contain credentials or internal URLs; preserve code only.
        outcome.error = f"ADP_ERROR: {response.error.code}"
        return outcome
    seen = set()
    for aic, raw_acs, match, _group in sorted(
        response.result.iter_agent_skills(), key=lambda row: (row[2].ranking, row[0])
    ):
        if match.skill_id != skill:
            outcome.warnings.append("ADP_SKILL_MISMATCH")
            continue
        try:
            acs = AgentCapabilitySpec.model_validate(raw_acs)
        except (ValidationError, TypeError, ValueError):
            outcome.warnings.append("ADP_INVALID_OR_MISSING_ACS")
            continue
        if not aic.strip() or acs.aic != aic or not acs.active:
            outcome.warnings.append("ADP_INACTIVE_OR_IDENTITY_MISMATCH")
            continue
        if skill not in {entry.id for entry in acs.skills}:
            outcome.warnings.append("ADP_ACS_SKILL_MISMATCH")
            continue
        if aic in seen:
            continue
        seen.add(aic)
        outcome.candidates.append({
            "aic": aic, "skillId": skill, "ranking": match.ranking,
            "acs": acs.model_dump(by_alias=True, exclude_none=True),
        })
    if not outcome.candidates:
        outcome.error = "ADP_NO_USABLE_CANDIDATE"
    return outcome


async def discover_for_task(
    task: ResearchTask,
    *,
    send: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    timeout_seconds: float = 10.0,
    limit: int = 5,
) -> DiscoveryOutcome:
    """Inject a separately verified transport; local tests inject an async fixture."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    request = build_discovery_request(task, limit=limit)
    try:
        async with asyncio.timeout(timeout_seconds):
            payload = await send(request.model_dump(by_alias=True, exclude_none=True))
        return parse_discovery_response(payload, task)
    except TimeoutError:
        return DiscoveryOutcome(required_skill(task), error="ADP_TIMEOUT")
    except Exception as exc:
        return DiscoveryOutcome(required_skill(task), error=f"ADP_TRANSPORT_ERROR: {type(exc).__name__}")
