"""统一 OpenAI-compatible 配置。默认复用现有 DeepSeek .env；不迁移或回显 Key。"""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from deepseek_model import DeepSeekModel, DeepSeekSettings, DeepSeekConfigError, DeepSeekCallError


@dataclass(frozen=True)
class ProviderSettings:
    api_key: str = field(repr=False)
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout_seconds: float = 60.0
    max_tokens: int = 4096

    def __post_init__(self):
        if self.provider not in {"deepseek", "qwen", "openai-compatible"}:
            raise DeepSeekConfigError("LLM_PROVIDER 仅支持 deepseek、qwen、openai-compatible")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise DeepSeekConfigError("LLM_BASE_URL 必须是无认证信息的 HTTPS API 地址")
        if not self.api_key.strip() or not self.model.strip():
            raise DeepSeekConfigError("缺少当前提供商的模型名或 Key；非 DeepSeek 不会复用 DeepSeek Key")
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 120:
            raise DeepSeekConfigError("调用超时必须在 1—120 秒内")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or not 512 <= self.max_tokens <= 8192:
            raise DeepSeekConfigError("最大输出长度必须为 512—8192 的整数")
        if self.provider == "deepseek":
            DeepSeekSettings(self.api_key, self.base_url, self.model, self.timeout_seconds, self.max_tokens)

    @classmethod
    def from_env(cls, env_file=None, environ=None):
        path = env_file or Path(__file__).resolve().parent / ".env"
        values = dict(dotenv_values(path, encoding="utf-8-sig", interpolate=False))
        process = os.environ if environ is None else environ
        values.update({k: v for k, v in process.items() if k.startswith(("LLM_", "DEEPSEEK_"))})
        provider = (values.get("LLM_PROVIDER") or "deepseek").strip()
        def get(name, fallback=""):
            value = values.get("LLM_" + name)
            if value is None and provider == "deepseek":
                value = values.get("DEEPSEEK_" + name)
            return str(value if value is not None else fallback).strip()
        try:
            return cls(
                api_key=get("API_KEY"), provider=provider,
                base_url=get("BASE_URL", "https://api.deepseek.com" if provider == "deepseek" else "").rstrip("/"),
                model=get("MODEL", "deepseek-flash" if provider == "deepseek" else ""),
                timeout_seconds=float(get("TIMEOUT_SECONDS", "60")),
                max_tokens=int(get("MAX_TOKENS", "4096")),
            )
        except (ValueError, TypeError) as exc:
            if isinstance(exc, DeepSeekConfigError):
                raise
            raise DeepSeekConfigError("LLM 数字配置无效") from None


class ProviderPlanningModel(DeepSeekModel):
    """复用已验证的 SDK 请求逻辑；Planner 本身只依赖 generate 协议。"""

    async def _complete(self, *args, **kwargs):
        try:
            return await super()._complete(*args, **kwargs)
        except DeepSeekCallError as exc:
            # 上层收到统一、脱敏的错误；不回显 API 响应体或 Key。
            raise DeepSeekCallError(str(exc).replace("DeepSeek", self.settings.provider)) from None
