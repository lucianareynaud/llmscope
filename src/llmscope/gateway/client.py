"""Provider-agnostic gateway client for LLM calls.

ARCHITECTURAL ROLE
──────────────────
This module is the single choke point for all LLM provider interactions.
Every call to an LLM in this application goes through ``call_llm()``.
No route or service ever imports a provider SDK directly.

PROVIDER ABSTRACTION
─────────────────────
All provider-specific logic is encapsulated behind ``ProviderBase`` in
``llmscope.gateway.provider``. This module consumes the base class — it
does not know whether the provider is OpenAI, Anthropic, Google, or a
custom implementation.

OTEL TRACING DESIGN
────────────────────
``call_llm()`` creates one OTel span that wraps the entire LLM operation,
including retries. This span is a child of the HTTP request span created
by FastAPIInstrumentor:

  HTTP POST /answer-routed   (FastAPIInstrumentor, kind=SERVER)
    └── chat gpt-4o-mini      (this module, kind=CLIENT)

SPAN LIFECYCLE
──────────────
  START  →  set request attributes (route, tier, model, max_output_tokens)
  SUCCESS →  set usage attributes (tokens_in, tokens_out, cost, cache_hit)
             leave span status UNSET (OTel convention: no error)
  ERROR  →  record_exception() + StatusCode.ERROR + error.type attribute
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from llmscope.envelope import (
    CostSource,
    EnvelopeStatus,
    LLMRequestEnvelope,
)
from llmscope.gateway.cost_model import estimate_cost
from llmscope.gateway.policies import (
    get_model_for_tier,
    get_route_policy,
)
from llmscope.gateway.provider import ProviderBase, get_provider
from llmscope.gateway.semconv import (
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MAX_TOKENS,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_GEN_AI_USAGE_INPUT_TOKENS,
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    VAL_GEN_AI_OPERATION_CHAT,
    VAL_GEN_AI_SYSTEM_OPENAI,
    resolve_attrs,
)
from llmscope.gateway.telemetry import emit

ModelTier = Literal["cheap", "expensive"]
GatewayRouteName = Literal["/answer-routed", "/conversation-turn"]

_tracer = trace.get_tracer(__name__, tracer_provider=None)


@dataclass(frozen=True)
class GatewayResult:
    """Structured result returned by the gateway for each LLM call.

    Frozen dataclass — immutable by design.
    """

    text: str
    selected_model: str
    request_id: str
    tokens_in: int
    tokens_out: int
    estimated_cost_usd: float
    cache_hit: bool


async def call_llm(
    prompt: str,
    model_tier: ModelTier,
    route_name: GatewayRouteName,
    metadata: dict[str, Any] | None = None,
) -> GatewayResult:
    """Execute one LLM call through the gateway.

    This is the only public entry point for LLM calls.

    Args:
        prompt:      Prepared prompt or context string.
        model_tier:  Logical tier ("cheap" or "expensive").
        route_name:  Gateway route identifier.
        metadata:    Optional route-specific key-values for telemetry.

    Returns:
        GatewayResult with response text, model, token counts, cost.

    Raises:
        ValueError: If provider SDK is not installed.
        Exception: Provider-specific exceptions after all retries.
    """
    request_id = str(uuid.uuid4())
    policy = get_route_policy(route_name)
    selected_model = get_model_for_tier(route_name, model_tier)
    provider = get_provider(policy.provider_name)

    telemetry_metadata = dict(metadata or {})
    telemetry_metadata["selected_model"] = selected_model

    span_name = f"{VAL_GEN_AI_OPERATION_CHAT} {selected_model}"

    with _tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
        request_attrs = resolve_attrs(
            {
                ATTR_GEN_AI_SYSTEM: VAL_GEN_AI_SYSTEM_OPENAI,
                ATTR_GEN_AI_OPERATION_NAME: VAL_GEN_AI_OPERATION_CHAT,
                ATTR_GEN_AI_REQUEST_MODEL: selected_model,
                ATTR_GEN_AI_REQUEST_MAX_TOKENS: policy.max_output_tokens,
            }
        )
        for key, value in request_attrs.items():
            span.set_attribute(key, value)

        span.set_attribute("llm_gateway.route", route_name)
        span.set_attribute("llm_gateway.model_tier", model_tier)
        span.set_attribute("llm_gateway.request_id", request_id)
        span.set_attribute(
            "llm_gateway.retry_attempts_allowed",
            policy.retry_attempts,
        )
        span.set_attribute("llm_gateway.cache_enabled", policy.cache_enabled)
        span.set_attribute("llm_gateway.provider", provider.provider_name)

        start_time = time.perf_counter()

        try:
            text, tokens_in, tokens_out = await _call_provider(
                provider=provider,
                prompt=prompt,
                model=selected_model,
                max_output_tokens=policy.max_output_tokens,
                retry_attempts=policy.retry_attempts,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000.0
            cost_usd = estimate_cost(selected_model, tokens_in, tokens_out)

            usage_attrs = resolve_attrs(
                {
                    ATTR_GEN_AI_USAGE_INPUT_TOKENS: tokens_in,
                    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS: tokens_out,
                }
            )
            for key, value in usage_attrs.items():
                span.set_attribute(key, value)

            span.set_attribute("llm_gateway.estimated_cost_usd", cost_usd)
            span.set_attribute("llm_gateway.cache_hit", False)

            envelope = LLMRequestEnvelope(
                schema_version="0.1.0",
                request_id=request_id,
                tenant_id="default",
                route=route_name,
                provider_selected=provider.provider_name,
                model_selected=selected_model,
                model_tier=model_tier,
                routing_decision=telemetry_metadata.get("routing_decision"),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_total=tokens_in + tokens_out,
                estimated_cost_usd=cost_usd,
                cost_source=CostSource.ESTIMATED_LOCAL_SNAPSHOT,
                latency_ms=latency_ms,
                status=EnvelopeStatus.OK,
                cache_hit=False,
            )

            emit(
                request_id=request_id,
                route=route_name,
                provider=provider.provider_name,
                model=selected_model,
                latency_ms=latency_ms,
                status="success",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                estimated_cost_usd=cost_usd,
                cache_hit=False,
                schema_valid=True,
                error_type=None,
                metadata=telemetry_metadata,
                envelope=envelope,
            )

            return GatewayResult(
                text=text,
                selected_model=selected_model,
                request_id=request_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                estimated_cost_usd=cost_usd,
                cache_hit=False,
            )

        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            error_type = provider.categorize_error(exc)

            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("llm_gateway.error_category", error_type)

            envelope = LLMRequestEnvelope(
                schema_version="0.1.0",
                request_id=request_id,
                tenant_id="default",
                route=route_name,
                provider_selected=provider.provider_name,
                model_selected=selected_model,
                model_tier=model_tier,
                routing_decision=telemetry_metadata.get("routing_decision"),
                tokens_in=0,
                tokens_out=0,
                tokens_total=0,
                estimated_cost_usd=0.0,
                cost_source=CostSource.DEGRADED_UNKNOWN,
                latency_ms=latency_ms,
                status=EnvelopeStatus.ERROR,
                error_type=error_type,
                cache_hit=False,
            )

            emit(
                request_id=request_id,
                route=route_name,
                provider=provider.provider_name,
                model=selected_model,
                latency_ms=latency_ms,
                status="error",
                tokens_in=0,
                tokens_out=0,
                estimated_cost_usd=0.0,
                cache_hit=False,
                schema_valid=True,
                error_type=error_type,
                metadata=telemetry_metadata,
                envelope=envelope,
            )

            raise


async def _call_provider(
    provider: ProviderBase,
    prompt: str,
    model: str,
    max_output_tokens: int,
    retry_attempts: int,
) -> tuple[str, int, int]:
    """Call the provider with bounded exponential backoff retry.

    Retry decision delegated to ``provider.is_retryable()``.

    Returns:
        Tuple of (response_text, tokens_in, tokens_out).
    """
    last_exception: Exception | None = None

    for attempt in range(retry_attempts + 1):
        try:
            response = await provider.complete(prompt, model, max_output_tokens)
            return (
                response.text,
                response.tokens_in,
                response.tokens_out,
            )

        except Exception as exc:
            last_exception = exc

            if provider.is_retryable(exc) and attempt < retry_attempts:
                await asyncio.sleep(2**attempt)
                continue

            break

    raise last_exception or RuntimeError("Provider call failed with no exception captured")
