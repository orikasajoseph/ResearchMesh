class LLMError(Exception):
    """Base exception for the ResearchMesh LLM provider."""


class LLMConfigurationError(LLMError):
    """Required provider configuration is missing or invalid."""


class LLMAuthenticationError(LLMError):
    """The provider rejected the configured credentials."""


class LLMRateLimitError(LLMError):
    """The provider rate limit remained active after retries."""


class LLMTimeoutError(LLMError):
    """The provider request timed out after retries."""


class LLMResponseError(LLMError):
    """The provider returned an empty or invalid response."""
