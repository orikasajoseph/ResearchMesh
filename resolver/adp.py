"""Official ADP/ACS models, with Wu's authenticated transport injected.

No inferred gateway, credentials, endpoint substitution or network fallback.
Candidate metadata is not proof of platform registration or reachability.
"""
import asyncio
from copy import deepcopy
import math
from time import perf_counter

from acps_sdk.acs.models import AgentCapabilitySpec
from acps_sdk.adp.models import DiscoveryRequest, DiscoveryResponse
from common.skills import SKILLS
from .base import AgentResolver, AgentHandle, NoMatchingAgentError


class AdpResolverError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AdpAgentResolver(AgentResolver):
    def __init__(self, send, executor_factory, *, skill_map=None, timeout_seconds=10, limit=5):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("INVALID_ADP_TIMEOUT")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("INVALID_ADP_LIMIT")
        self.send, self.executor_factory = send, executor_factory
        self.skill_map = dict({v: v for v in SKILLS.values()} if skill_map is None else skill_map)
        if any(k not in SKILLS.values() or not isinstance(v, str) or not v.strip()
               for k, v in self.skill_map.items()):
            raise ValueError("INVALID_SKILL_MAPPING")
        self.timeout_seconds, self.limit = timeout_seconds, limit
        self.records = []

    async def resolve(self, required_capability):
        business_skill = required_capability  # historical base argument name; represents a business skill
        if business_skill not in self.skill_map:
            raise AdpResolverError("UNMAPPED_BUSINESS_SKILL")
        platform_skill = self.skill_map[business_skill]
        request = DiscoveryRequest(type="filtered", limit=self.limit, filter={"conditions": [
            {"field": "active", "op": "eq", "value": True},
            {"field": "skills.id", "op": "eq", "value": platform_skill}]}).model_dump(by_alias=True, exclude_none=True)
        record = {"requiredSkill": business_skill, "platformSkillId": platform_skill,
                  "scope": "ADP_COMPONENT_INJECTED_TRANSPORT", "request": deepcopy(request),
                  "agentAic": None, "warnings": [], "error": None, "latencyMs": None}
        started = perf_counter()
        try:
            try:
                raw = await asyncio.wait_for(self.send(deepcopy(request)), self.timeout_seconds)
            except asyncio.TimeoutError:
                raise AdpResolverError("ADP_TIMEOUT") from None
            except Exception:
                raise AdpResolverError("ADP_TRANSPORT_ERROR") from None
            try:
                response = DiscoveryResponse.model_validate(raw)
            except (ValueError, TypeError):
                raise AdpResolverError("ADP_INVALID_RESPONSE") from None
            if response.error is not None:
                raise AdpResolverError("ADP_SERVER_ERROR")
            seen, candidates = set(), []
            for aic, raw_acs, match, _ in sorted(response.result.iter_agent_skills(), key=lambda row: (row[2].ranking, row[0])):
                if match.skill_id != platform_skill:
                    record["warnings"].append("ADP_SKILL_MISMATCH"); continue
                try:
                    acs = AgentCapabilitySpec.model_validate(raw_acs)
                except (ValueError, TypeError):
                    record["warnings"].append("ADP_INVALID_ACS"); continue
                if (not aic.strip() or acs.aic != aic or not acs.active
                        or platform_skill not in {s.id for s in acs.skills}):
                    record["warnings"].append("ADP_IDENTITY_ACTIVITY_OR_SKILL_MISMATCH"); continue
                if aic in seen:
                    continue
                seen.add(aic)
                candidates.append({"aic": aic, "skillId": platform_skill, "ranking": match.ranking,
                                   "acs": acs.model_dump(by_alias=True, exclude_none=True)})
            if not candidates:
                raise NoMatchingAgentError("ADP_NO_USABLE_CANDIDATE")
            selected = candidates[0]
            # Factory must select/verify transport and identities; preserve security requirements.
            # Never silently downgrade mTLS to unauthenticated HTTP or try another paid Agent.
            try:
                executor = self.executor_factory(deepcopy(selected))
            except Exception:
                raise AdpResolverError("ADP_EXECUTOR_CONFIGURATION_REQUIRED") from None
            if not callable(getattr(executor, "execute", None)):
                raise AdpResolverError("ADP_INVALID_EXECUTOR")
            record.update(agentAic=selected["aic"], ranking=selected["ranking"], candidateCount=len(candidates))
            return AgentHandle(selected["aic"], (business_skill,), executor)
        except (AdpResolverError, NoMatchingAgentError) as error:
            record["error"] = getattr(error, "code", "ADP_NO_USABLE_CANDIDATE")
            raise
        except Exception:
            record["error"] = "ADP_INVALID_RESPONSE"
            raise AdpResolverError("ADP_INVALID_RESPONSE") from None
        finally:
            record["latencyMs"] = round((perf_counter() - started) * 1000, 3)
            self.records.append(record)
