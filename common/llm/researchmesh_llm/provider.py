import json
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from .config import LLMConfig
from .exceptions import (
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from .schemas import ChatMessage, LLMResponse, LLMUsage


class LLMProvider:
    def __init__(
        self,
        config: LLMConfig | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.config = config or LLMConfig.from_env()
        self.client = client or OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )

    def chat(
        self,
        messages: list[ChatMessage | dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return self._chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=None,
        )

    def generate_json(
        self,
        messages: list[ChatMessage | dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_messages(messages)
        instruction = "Return only one valid JSON object. Do not use Markdown fences."
        if schema is not None:
            instruction += " The JSON must follow this schema: " + json.dumps(
                schema, ensure_ascii=False, separators=(",", ":")
            )
        json_messages = [{"role": "system", "content": instruction}, *normalized]

        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = self._chat(
                    json_messages,
                    temperature=0,
                    max_tokens=None,
                    response_format={"type": "json_object"},
                )
                parsed = json.loads(response.content)
                if not isinstance(parsed, dict):
                    raise LLMResponseError("The model response is not a JSON object")
                return parsed
            except (json.JSONDecodeError, LLMResponseError) as exc:
                last_error = exc

        raise LLMResponseError(
            "The model did not return valid JSON after one retry"
        ) from last_error

    def _chat(
        self,
        messages: list[ChatMessage | dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict[str, str] | None,
    ) -> LLMResponse:
        normalized = self._normalize_messages(messages)
        started_at = time.perf_counter()
        warnings: list[str] = []

        for attempt in range(self.config.max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.config.model,
                    "messages": normalized,
                }
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if response_format is not None:
                    kwargs["response_format"] = response_format

                response = self.client.chat.completions.create(**kwargs)
                if not response.choices:
                    raise LLMResponseError("The model returned no choices")
                content = response.choices[0].message.content
                if not content:
                    raise LLMResponseError("The model returned empty content")
                if attempt:
                    warnings.append(f"Request succeeded after {attempt} retry attempts.")
                return LLMResponse(
                    model=response.model or self.config.model,
                    content=content,
                    latencyMs=round((time.perf_counter() - started_at) * 1000, 3),
                    usage=self._usage(response.usage),
                    requestId=getattr(response, "id", None),
                    warnings=warnings,
                )
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise LLMAuthenticationError(
                    "DeepSeek authentication or permission validation failed"
                ) from exc
            except RateLimitError as exc:
                if attempt >= self.config.max_retries:
                    raise LLMRateLimitError(
                        "DeepSeek rate limit remained active after retries"
                    ) from exc
                self._backoff(attempt)
            except APITimeoutError as exc:
                if attempt >= self.config.max_retries:
                    raise LLMTimeoutError("DeepSeek request timed out") from exc
                self._backoff(attempt)
            except APIConnectionError as exc:
                if attempt >= self.config.max_retries:
                    raise LLMResponseError(
                        "Unable to connect to the DeepSeek API"
                    ) from exc
                self._backoff(attempt)
            except APIStatusError as exc:
                if exc.status_code in (401, 403):
                    raise LLMAuthenticationError(
                        "DeepSeek authentication or permission validation failed"
                    ) from exc
                if exc.status_code == 429:
                    if attempt < self.config.max_retries:
                        self._backoff(attempt)
                        continue
                    raise LLMRateLimitError(
                        "DeepSeek rate limit remained active after retries"
                    ) from exc
                raise LLMResponseError(
                    f"DeepSeek returned HTTP status {exc.status_code}"
                ) from exc

        raise LLMResponseError("DeepSeek request failed")

    @staticmethod
    def _normalize_messages(
        messages: list[ChatMessage | dict[str, str]],
    ) -> list[dict[str, str]]:
        return [
            ChatMessage.model_validate(message).model_dump()
            for message in messages
        ]

    @staticmethod
    def _usage(usage: Any) -> LLMUsage | None:
        if usage is None:
            return None
        return LLMUsage(
            promptTokens=getattr(usage, "prompt_tokens", None),
            completionTokens=getattr(usage, "completion_tokens", None),
            totalTokens=getattr(usage, "total_tokens", None),
        )

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 8))
