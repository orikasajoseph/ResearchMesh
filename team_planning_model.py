"""Opt-in Planner bridge to Zhang's public synchronous LLMProvider.chat API.

Keep the working async adapter as default. No changes to the team Provider.
One chat per generate; disable its retries so Planner's two attempts stay bounded.
"""
import asyncio
import json

from common.llm.researchmesh_llm import LLMConfig, LLMProvider
from common.models import Plan
from deepseek_model import DeepSeekCallError


class TeamPlanningModel:
    def __init__(self, settings, *, provider=None):
        self.settings = settings
        if provider is None:
            config = LLMConfig(api_key=settings.api_key, base_url=settings.base_url,
                               model=settings.model, timeout_seconds=settings.timeout_seconds, max_retries=0)
            provider = LLMProvider(config=config)
        if provider.config.max_retries != 0:
            raise ValueError("TEAM_PROVIDER_RETRIES_MUST_BE_ZERO")
        self.provider = provider
        self.request_count, self.last_usage = 0, {}
        self._pending, self._uncertain = None, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        # Cancelling to_thread cannot stop an in-flight HTTP request. Wait for the
        # configured Provider timeout before closing; never send a replacement.
        if self._pending is not None:
            try:
                await asyncio.shield(self._pending)
            except Exception:
                pass
        close = getattr(getattr(self.provider, "client", None), "close", None)
        if close:
            close()

    async def generate(self, instructions, user_query):
        if self._uncertain or (self._pending is not None and not self._pending.done()):
            raise DeepSeekCallError("TEAM_PROVIDER_PREVIOUS_REQUEST_UNCERTAIN")
        prompt = instructions + "\nReturn one JSON object matching this schema:\n" + json.dumps(Plan.model_json_schema(), ensure_ascii=False)
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user_query}]
        self.request_count += 1
        self._pending = asyncio.create_task(asyncio.to_thread(self.provider.chat, messages,
                             temperature=0, max_tokens=self.settings.max_tokens))
        try:
            response = await asyncio.shield(self._pending)
        except asyncio.CancelledError:
            self._uncertain = True
            raise
        except Exception:
            raise DeepSeekCallError("TEAM_PROVIDER_CALL_FAILED") from None
        self.last_usage = response.usage.model_dump() if response.usage is not None else {}
        return response.content
