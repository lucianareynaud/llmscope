# LLMScope

LLMScope is a reference implementation of an LLM control plane that answers three questions most production teams cannot: what did that request cost, which model served it, and why. It demonstrates gateway-based cost attribution, OpenTelemetry instrumentation aligned with GenAI semantic conventions, a provider-agnostic abstraction, a versioned envelope contract for request lifecycle, deterministic evaluation, and offline reporting — built as an inspectable engineering artifact, not a demo.

## Why this exists

Production LLM systems require cost visibility, model routing transparency, and request-level attribution. Most implementations scatter provider calls across route handlers, making complete telemetry impossible. LLMScope enforces a single gateway choke point (`call_llm()`) through which all provider traffic flows, enabling accurate cost tracking, structured context propagation, and deterministic offline analysis without runtime dependencies on external observability vendors.

This repository demonstrates the control-plane layer for accountable AI systems: the instrumentation, attribution, and evidence infrastructure that makes LLM operations measurable and auditable.

## What this repository demonstrates

**Implemented and tested:**

- **Gateway choke point** — `src/llmscope/gateway/client.py` is the sole path for all LLM provider calls. No route handler calls providers directly. This architectural invariant makes cost attribution complete.

- **Provider abstraction** — `ProviderBase` defines a minimal contract (2 abstract methods, 2 optional). `OpenAIProvider` and `AnthropicProvider` are built-in. Adding Google, Bedrock, or custom providers requires subclassing `ProviderBase` and registering — no changes to gateway or route logic.

- **Versioned envelope contract** — `LLMRequestEnvelope` (v0.1.0) defines 6 semantic blocks: identity/context, model selection, economics, reliability, governance, cache/eval. Every gateway call constructs an envelope and serializes it to JSONL, making local artifacts a direct representation of the typed contract.

- **Structured attribution context** — `LLMRequestContext` propagates tenant_id, caller_id, use_case, feature_id, experiment_id, and budget_namespace through the call stack and into telemetry without framework coupling.

- **OpenTelemetry instrumentation** — each gateway call emits an OTel CLIENT span (nested under FastAPI SERVER span) and records 4 metric instruments: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, `llm_gateway.estimated_cost_usd`, `llm_gateway.requests`. Backend-agnostic via OTLP.

- **Semantic convention isolation** — `src/llmscope/gateway/semconv.py` centralizes GenAI attribute names and implements `resolve_attrs()` for dual-emission migration via `OTEL_SEMCONV_STABILITY_OPT_IN`. No other module imports from `opentelemetry.semconv._incubating`.

- **Dual telemetry emission** — parallel to OTel, each call serializes the envelope as JSON to `artifacts/logs/telemetry.jsonl`. Supports offline analysis without a running collector.

- **Deterministic cost model** — local pricing snapshot for OpenAI and Anthropic. Per-request USD estimation without external billing lookup. Reproducible and auditable.

- **Keyword-based routing** — deterministic complexity classifier assigns model tiers (cheap/expensive) based on keywords and message length. Intentionally simple and inspectable.

- **Context budget management** — token counting via tiktoken, configurable strategies (full, sliding_window, summarized), raises `ContextTooLargeError` when budget exceeded.

- **Dataset-driven evaluation** — 3 eval runners (classify, answer_routed, conversation_turn) using FastAPI TestClient with deterministic mocks. Checks schema compliance, required fields, routing metadata. Not semantic evaluation.

- **Deterministic reporting** — markdown report generator from JSONL telemetry. Per-route aggregates, Pareto analysis, before/after comparison mode. Example artifacts included (10 synthetic events + generated report).

- **Auth and rate limiting** — API key validation via X-API-Key header (constant-time comparison), per-caller sliding window rate limiting (in-memory deque).

**257 tests, GitHub Actions CI (ruff, mypy, pytest).**

## Architecture

Current implementation (v0.1.0):

```
HTTP Request
  └── APIKeyMiddleware          X-API-Key validation, 401 on failure
        └── RateLimitMiddleware per-key sliding window, 429 on overflow
              └── FastAPIInstrumentor OTel SERVER span
                    └── Route Handler
                          ├── /classify-complexity → determine_complexity() [no LLM call]
                          ├── /answer-routed       → determine_complexity() → call_llm()
                          └── /conversation-turn   → prepare_context() → call_llm()

                                   call_llm() [src/llmscope/gateway/client.py]
                                     ├── RoutePolicy lookup (tier → model mapping)
                                     ├── LLMRequestContext resolution
                                     ├── OTel CLIENT span start
                                     ├── Retry loop with exponential backoff
                                     ├── ProviderBase.complete() → OpenAI/Anthropic
                                     ├── estimate_cost()
                                     ├── LLMRequestEnvelope construction
                                     └── emit() → OTel metrics + JSONL
```

The non-negotiable invariant: every LLM provider call passes through `call_llm()`. No route, service, or middleware calls provider APIs directly.

## Core components

**src/llmscope/** — pip-installable library, runtime-agnostic

- `envelope.py` — `LLMRequestEnvelope` dataclass, `EnvelopeStatus`, `CostSource`, `CircuitState` enums
- `context.py` — `LLMRequestContext` for structured attribution
- `semconv.py` — `llmscope.*` OTel attribute constants
- `gateway/client.py` — `call_llm()`, `GatewayResult`
- `gateway/provider.py` — `ProviderBase`, `OpenAIProvider`, `AnthropicProvider`
- `gateway/telemetry.py` — `emit()` dual-write (OTel + JSONL)
- `gateway/cost_model.py` — `estimate_cost()`, hardcoded pricing
- `gateway/policies.py` — `RoutePolicy`, tier-to-model mapping
- `gateway/otel_setup.py` — `setup_otel()`, `shutdown_otel()`
- `gateway/semconv.py` — `gen_ai.*` attribute constants, `resolve_attrs()`

**app/** — reference FastAPI application (not pip-installed)

- `main.py` — FastAPI app with lifespan OTel setup
- `middleware/auth.py` — API key validation
- `middleware/rate_limit.py` — per-key sliding window
- `routes/` — 3 routes + health endpoints
- `services/routing.py` — `determine_complexity()`
- `services/context_manager.py` — `prepare_context()`
- `services/token_counter.py` — `count_tokens()`
- `schemas/` — Pydantic request/response contracts

**evals/** — evaluation harness (not pip-installed)

- `runners/run_classify_eval.py`
- `runners/run_answer_routed_eval.py`
- `runners/run_conversation_turn_eval.py`
- `datasets/*.jsonl` — test cases
- `assertions/` — validation logic

**reporting/** — report generator (not pip-installed)

- `make_report.py` — CLI tool, markdown output

**examples/** — pre-generated artifacts

- `sample_telemetry.jsonl` — 10 synthetic events
- `sample_report.md` — generated report

## Repository structure

```
llmscope/
├── src/llmscope/              ← pip-installable library
│   ├── __init__.py            ← public API surface
│   ├── py.typed               ← PEP 561 marker
│   ├── envelope.py
│   ├── context.py
│   ├── semconv.py
│   └── gateway/
│       ├── client.py          ← call_llm() — the choke point
│       ├── provider.py
│       ├── telemetry.py
│       ├── cost_model.py
│       ├── policies.py
│       ├── otel_setup.py
│       └── semconv.py
├── app/                       ← reference FastAPI app
├── evals/                     ← eval harness
├── reporting/                 ← report generator
├── examples/                  ← sample artifacts
└── tests/                     ← 257 tests
```

## Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Required environment variables:

```bash
export OPENAI_API_KEY="your_key"      # if using OpenAI
export ANTHROPIC_API_KEY="your_key"   # if using Anthropic
export APP_API_KEY="your_app_key"
```

Optional configuration (defaults shown):

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318  # OTLP collector
OTEL_SDK_DISABLED=false                             # set true in CI
RATE_LIMIT_RPM=60
MAX_CONTEXT_TOKENS=8192
```

Run the reference app:

```bash
uvicorn app.main:app --reload
```

Interactive docs at `http://localhost:8000/docs`.

## Validation

Run tests:

```bash
OTEL_SDK_DISABLED=true python3 -m pytest tests/ -q
```

Linting and type checking:

```bash
python3 -m ruff check .
python3 -m ruff format --check .
python3 -m mypy src/llmscope/ app/ evals/ reporting/ --ignore-missing-imports
```

Run eval harness:

```bash
OTEL_SDK_DISABLED=true python3 -m evals.runners.run_classify_eval
OTEL_SDK_DISABLED=true python3 -m evals.runners.run_answer_routed_eval
OTEL_SDK_DISABLED=true python3 -m evals.runners.run_conversation_turn_eval
```

Generate report from example telemetry:

```bash
python3 -m reporting.make_report \
  --after-log examples/sample_telemetry.jsonl \
  --output examples/sample_report.md
```

## Current limitations and non-goals

**Not implemented (envelope schema supports, but no runtime logic):**

- Semantic cache — envelope has `cache_hit`, `cache_strategy`, `cache_key_fingerprint` fields, but no cache implementation in gateway
- Circuit breaker — envelope has `circuit_state` field, but no circuit breaker logic
- Provider fallback — envelope has `fallback_triggered`, `fallback_reason` fields, but `call_llm()` uses single provider per request
- Policy engine — envelope has `policy_decision`, `policy_mode` fields, but no policy evaluation beyond route-level tier mapping
- PII detection/redaction — envelope has `redaction_applied`, `pii_detected` fields, but no implementation

These are **architectural affordances** in the envelope contract, not working features. The envelope schema is forward-compatible; implementations can be added without breaking the contract.

**Intentionally out of scope:**

- Streaming responses
- Tool/function calling
- Multi-tenancy (single APP_API_KEY, no tenant isolation)
- Distributed rate limiting (in-memory only, no Redis)
- Semantic evaluation (schema checks only)
- Production deployment configuration
- Dashboard or UI

**Routing classifier limitations:**

The keyword-based classifier in `app/services/routing.py` is intentionally generic. Production use requires domain-specific keyword tuning and threshold calibration based on actual traffic analysis. The repository demonstrates the instrumentation infrastructure that makes that calibration loop measurable, not the calibrated classifier itself.

## Stability guarantees

LLMScope follows [Semantic Versioning](https://semver.org/). From v0.1.0:

**Stable public API:**

```python
from llmscope import (
    LLMRequestEnvelope, LLMRequestContext, GatewayResult,
    ProviderBase, ProviderResponse,
    call_llm, estimate_cost, setup_otel, shutdown_otel,
    OpenAIProvider, AnthropicProvider,
)
```

**Provider contract:** `ProviderBase` has 2 abstract methods (`provider_name`, `complete`) and 2 default methods (`is_retryable`, `categorize_error`). New optional methods with safe defaults may be added in minor versions. New abstract methods require a major version bump.

**JSONL schema:** telemetry events include `schema_version` field. New fields may appear in minor versions. Consumers should tolerate unknown keys.

**OTel attributes:** `llmscope.*` attribute names in `src/llmscope/semconv.py` are stable from v0.1.0. `gen_ai.*` attributes follow OpenTelemetry GenAI Semantic Conventions and may change via `OTEL_SEMCONV_STABILITY_OPT_IN`.

**Cost model:** pricing values are configuration, not API. May be updated in any version to reflect current provider pricing.

## Near-term roadmap

**Planned (envelope schema already supports):**

- Exact cache implementation (deterministic fingerprinting, hit/miss tracking)
- Circuit breaker (three-state machine, failure threshold, timeout)
- Provider fallback (primary → secondary on error)
- Policy engine (typed policy evaluation, budget gates)

**Under consideration:**

- SDK distribution (library-only, no separate process)
- Sidecar distribution (out-of-band event collection)
- Redis-backed rate limiting
- Embedding-based routing classifier

## Related projects

- [llm-eval-gate](https://github.com/lucianareynaud/llm-eval-gate) — Evidence-based quality gate consuming LLMScope telemetry for deployment decisions

## Development

Specs under `.kiro/` document architecture decisions, instrumentation boundaries, and semantic convention alignment. The repository reflects production experience with OpenTelemetry GenAI conventions and cost attribution in regulated environments.

## License

MIT License - see [LICENSE](LICENSE) file for details.
