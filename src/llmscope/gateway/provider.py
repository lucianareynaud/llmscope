"""Provider abstraction for the LLMScope gateway.

ARCHITECTURAL ROLE
──────────────────
This module decouples the gateway from any specific LLM provider SDK.
The gateway (client.py) handles retry, telemetry, cost estimation, OTel spans,
and envelope construction. The provider handles ONLY the API call and response
normalization.

This separation means adding a new provider requires:
1. Subclass ProviderBase (one class, two required methods).
2. Register via register_provider() or auto-registration.
3. Add pricing to cost_model.py.
4. No changes to client.py, telemetry.py, or any route handler.

BACKWARD COMPATIBILITY CONTRACT
─────────────────────────────────
ProviderBase uses an ABC with abstract + default methods, not a Protocol.
This is a deliberate design decision for forward-compatible evolution:

  ABSTRACT (must override — breaking to add new ones in minor versions):
    - provider_name     — stable since v0.1.0
    - complete()        — stable since v0.1.0

  DEFAULT (safe to add new ones — existing subclasses inherit the default):
    - is_retryable()    — default returns False (no retry)
    - categorize_error() — default returns "unknown"

If a future version adds supports_streaming() or estimate_tokens(), it will
be a default method. Existing third-party providers will not break.

Adding a new abstract method is a MAJOR version bump (breaking change).

BUILT-IN PROVIDERS
───────────────────
  OpenAIProvider    — requires ``pip install llmscope[openai]``
  AnthropicProvider — requires ``pip install llmscope[anthropic]``

Both are optional extras. The core package (envelope, telemetry, gateway)
installs with zero provider SDKs.

AUTO-REGISTRATION
──────────────────
At module import time, each built-in provider checks whether its SDK is
importable. If yes, an instance is registered in the module-level registry.
If not, the provider is silently skipped.

TESTING
────────
Tests that call ``call_llm()`` should patch ``gateway.client.get_provider``
to return a mock provider:

    mock_provider = Mock(spec=ProviderBase)
    mock_provider.provider_name = "mock"
    mock_provider.complete = AsyncMock(return_value=ProviderResponse(...))

    with patch("gateway.client.get_provider", return_value=mock_provider):
        result = await call_llm(...)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderResponse:
    """Normalized response from any LLM provider.

    Frozen dataclass — immutable after creation. The gateway consumes this
    directly for telemetry, cost estimation, and envelope construction.
    """

    text: str
    tokens_in: int
    tokens_out: int


class ProviderBase(ABC):
    """Base class for LLM provider adapters.

    The gateway calls ``complete()`` and handles everything else:
    retry with exponential backoff, OTel span lifecycle, cost estimation,
    envelope construction, metric emission, JSONL artifact.

    Subclass contract:
      REQUIRED (abstract — must override):
        provider_name   — stable identifier for telemetry
        complete()      — make the API call and normalize the response

      OPTIONAL (have safe defaults — override for provider-specific behavior):
        is_retryable()    — default: return False (never retry)
        categorize_error() — default: return "unknown"

    This design ensures new optional methods can be added in minor versions
    without breaking existing subclasses.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable identifier for telemetry attributes.

        This string appears in OTel span attributes, JSONL telemetry events,
        and the LLMRequestEnvelope.provider_selected field. Must be lowercase,
        stable across releases, and never contain version info.

        Examples: ``"openai"``, ``"anthropic"``, ``"google"``, ``"bedrock"``
        """
        ...

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        model: str,
        max_output_tokens: int,
    ) -> ProviderResponse:
        """Execute one completion call and return normalized response.

        Called inside the gateway's retry loop. Must make exactly one API
        call and either return a ``ProviderResponse`` or raise an exception.
        The gateway handles retry decisions via ``is_retryable()``.

        Args:
            prompt: Prepared prompt string.
            model: Concrete model name (e.g. ``"gpt-4o-mini"``).
            max_output_tokens: Token cap for the response.

        Returns:
            ProviderResponse with normalized text and token counts.

        Raises:
            Any exception from the provider SDK. The gateway will call
            ``is_retryable()`` and ``categorize_error()`` on it.
        """
        ...

    def is_retryable(self, error: Exception) -> bool:
        """Return True if the error should trigger a retry attempt.

        Override with provider-specific retry logic. Default: returns False
        (never retry). This is the safe default because retrying an unknown
        error can cause duplicate requests.
        """
        return False

    def categorize_error(self, error: Exception) -> str:
        """Map an exception to a stable telemetry error category string.

        Override with provider-specific error classification. Default:
        returns "unknown".

        Use a consistent taxonomy across providers:
          ``auth_error``       — 401/403
          ``rate_limit``       — 429
          ``timeout``          — network timeout
          ``transient_error``  — network failure or 5xx
          ``invalid_request``  — 400/404/422
          ``unknown``          — none of the above
        """
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Provider Registry
# ─────────────────────────────────────────────────────────────────────────────

_registry: dict[str, ProviderBase] = {}


def register_provider(name: str, provider: ProviderBase) -> None:
    """Register a provider implementation by name."""
    _registry[name] = provider


def get_provider(name: str) -> ProviderBase:
    """Resolve a registered provider by name.

    Raises:
        ValueError: If the provider is not registered.
    """
    if name not in _registry:
        installed = list(_registry.keys()) or ["none"]
        raise ValueError(
            f"Provider '{name}' is not registered. "
            f"Installed providers: {', '.join(installed)}. "
            f"Install the SDK: pip install llmscope[{name}]"
        )
    return _registry[name]


def available_providers() -> list[str]:
    """Return names of all currently registered providers."""
    return list(_registry.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Built-in: OpenAI
# ─────────────────────────────────────────────────────────────────────────────


class OpenAIProvider(ProviderBase):
    """OpenAI provider via the Responses API.

    Requires: ``pip install llmscope[openai]``
    Env var: ``OPENAI_API_KEY``
    """

    @property
    def provider_name(self) -> str:
        return "openai"

    async def complete(
        self,
        prompt: str,
        model: str,
        max_output_tokens: int,
    ) -> ProviderResponse:
        from openai import AsyncOpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        client = AsyncOpenAI(api_key=api_key)
        response = await client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_output_tokens,
        )

        text = response.output_text or ""
        usage = response.usage
        tokens_in = usage.input_tokens if usage else 0
        tokens_out = usage.output_tokens if usage else 0

        return ProviderResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out)

    def is_retryable(self, error: Exception) -> bool:
        import openai

        return isinstance(
            error,
            (
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ),
        )

    def categorize_error(self, error: Exception) -> str:
        import openai

        if isinstance(error, openai.AuthenticationError):
            return "auth_error"
        if isinstance(error, openai.PermissionDeniedError):
            return "auth_error"
        if isinstance(error, openai.RateLimitError):
            return "rate_limit"
        if isinstance(error, openai.APITimeoutError):
            return "timeout"
        if isinstance(error, openai.APIConnectionError):
            return "transient_error"
        if isinstance(error, openai.InternalServerError):
            return "transient_error"
        if isinstance(
            error,
            (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError),
        ):
            return "invalid_request"
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Built-in: Anthropic
# ─────────────────────────────────────────────────────────────────────────────


class AnthropicProvider(ProviderBase):
    """Anthropic provider via the Messages API.

    Requires: ``pip install llmscope[anthropic]``
    Env var: ``ANTHROPIC_API_KEY``
    """

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def complete(
        self,
        prompt: str,
        model: str,
        max_output_tokens: int,
    ) -> ProviderResponse:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text if response.content else ""  # type: ignore[union-attr]
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        return ProviderResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out)

    def is_retryable(self, error: Exception) -> bool:
        try:
            import anthropic
        except ImportError:
            return False

        return isinstance(
            error,
            (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ),
        )

    def categorize_error(self, error: Exception) -> str:
        try:
            import anthropic
        except ImportError:
            return "unknown"

        if isinstance(error, anthropic.AuthenticationError):
            return "auth_error"
        if isinstance(error, anthropic.PermissionDeniedError):
            return "auth_error"
        if isinstance(error, anthropic.RateLimitError):
            return "rate_limit"
        if isinstance(error, anthropic.APITimeoutError):
            return "timeout"
        if isinstance(error, anthropic.APIConnectionError):
            return "transient_error"
        if isinstance(error, anthropic.InternalServerError):
            return "transient_error"
        if isinstance(error, (anthropic.BadRequestError, anthropic.NotFoundError)):
            return "invalid_request"
        return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Auto-registration at import time
# ─────────────────────────────────────────────────────────────────────────────

try:
    import openai as _openai_probe  # noqa: F401

    register_provider("openai", OpenAIProvider())
except ImportError:
    pass

try:
    import anthropic as _anthropic_probe  # noqa: F401

    register_provider("anthropic", AnthropicProvider())
except ImportError:
    pass
