"""LLMScope — Observable cost control for production LLMs.

Public API surface. Import from here for stable, versioned access.
Internal module paths (llmscope.gateway.client, etc.) are implementation
details and may change between minor versions.
"""

from llmscope.envelope import (
    CostSource,
    EnvelopeStatus,
    LLMRequestEnvelope,
)
from llmscope.gateway.client import GatewayResult, call_llm
from llmscope.gateway.cost_model import estimate_cost, get_pricing
from llmscope.gateway.otel_setup import setup_otel, shutdown_otel
from llmscope.gateway.policies import (
    RoutePolicy,
    get_model_for_tier,
    get_route_policy,
)
from llmscope.gateway.provider import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderBase,
    ProviderResponse,
    available_providers,
    get_provider,
    register_provider,
)

__version__ = "0.1.0"

__all__ = [
    "AnthropicProvider",
    "CostSource",
    "EnvelopeStatus",
    "GatewayResult",
    "LLMRequestEnvelope",
    "OpenAIProvider",
    "ProviderBase",
    "ProviderResponse",
    "RoutePolicy",
    "__version__",
    "available_providers",
    "call_llm",
    "estimate_cost",
    "get_model_for_tier",
    "get_pricing",
    "get_provider",
    "get_route_policy",
    "register_provider",
    "setup_otel",
    "shutdown_otel",
]
