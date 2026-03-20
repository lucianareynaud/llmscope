# LLMScope

Most teams operating LLMs in production cannot answer three questions with confidence: what did that request cost, which model actually served it, and why. LLMScope is a reference implementation of the control-plane layer that makes those answers trivially available.

It implements gateway-based cost attribution, OpenTelemetry instrumentation aligned with the GenAI semantic conventions, model-tier routing, context budget management, a provider-agnostic abstraction, a versioned envelope contract for request lifecycle, bounded regression detection, and deterministic reporting — built as an inspectable, runnable engineering artifact rather than a demo.

## Architecture

```
HTTP Request
  └── APIKeyMiddleware          auth: X-API-Key header
        └── RateLimitMiddleware per-key sliding window, 429 on overflow
              └── FastAPIInstrumentor OTel SERVER span, W3C trace propagation
                    └── Route Handler
                          ├── /classify-complexity → determine_complexity() [no LLM call]
                          ├── /answer-routed       → determine_complexity() → call_llm()
                          └── /conversation-turn   → prepare_context() → call_llm()

                                   call_llm() [src/llmscope/gateway/client.py — OTel CLIENT span]
                                     ├── RoutePolicy lookup
                                     ├── ProviderBase.complete()  → OpenAIProvider (default)
                                     ├── estimate_cost()
                                     ├── LLMRequestEnvelope construction
                                     └── emit()  → OTel metrics + envelope-based JSONL
```

The non-negotiable architectural invariant: every LLM provider call passes through `src/llmscope/gateway/client.py`. No route handler, service, or middleware calls any provider API directly. This is what makes cost attribution complete and telemetry accurate.

## What is implemented

**Gateway** — `src/llmscope/gateway/client.py` is the single choke point for all LLM provider calls. Enforces route policy, measures latency, estimates cost, emits telemetry, handles retry with exponential backoff, and returns normalized results.

**Provider abstraction** — `src/llmscope/gateway/provider.py` defines `ProviderBase`, an abstract base class that any LLM provider must subclass. Only two methods are required (`provider_name`, `complete`); error classification methods have safe defaults. `OpenAIProvider` and `AnthropicProvider` are included as built-in implementations. To add Google, Bedrock, or any other provider: subclass `ProviderBase`, register, and add pricing to the cost model. No other module changes.

**LLMRequestEnvelope** — `src/llmscope/envelope.py` defines the versioned, runtime-agnostic contract for LLM request lifecycle. Six semantic blocks: identity/context, model selection, economics, reliability, governance, cache/eval. The envelope is constructed on every gateway call and serialized into the JSONL telemetry artifact, making the local artifact a direct representation of the typed contract.

**OpenTelemetry instrumentation** — each gateway call produces an OTel `CLIENT` span nested under the FastAPI `SERVER` span and emits four metric instruments aligned with the GenAI semantic conventions: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, `llm_gateway.estimated_cost_usd`, `llm_gateway.requests`. Backend-agnostic via OTLP — works unchanged with Grafana, Datadog, Jaeger, Honeycomb, or a self-hosted collector.

**Semantic convention isolation** — `src/llmscope/gateway/semconv.py` centralizes all GenAI attribute name strings and implements `resolve_attrs()` for dual-emission migration support via `OTEL_SEMCONV_STABILITY_OPT_IN`. No other module imports from `opentelemetry.semconv._incubating` directly.

**Structured local telemetry** — in parallel with OTel emission, each gateway call serializes the `LLMRequestEnvelope` as a JSON line to `artifacts/logs/telemetry.jsonl`. Supports offline analysis and reporting without a running collector.

**Cost model** — deterministic local pricing snapshot for OpenAI and Anthropic models. Per-request USD cost estimation without external billing lookup. Reproducible and auditable.

**Routing classifier** — deterministic keyword and length-based complexity classification. Assigns model tiers (cheap/expensive) for cost-aware routing. Intentionally simple and inspectable — see "Adapting the routing classifier" below.

**Context management** — three configurable strategies: `full`, `sliding_window`, `summarized`. Token counting via tiktoken. Raises `ContextTooLargeError` when assembled context exceeds budget.

**Eval harness** — dataset-driven regression runners using FastAPI `TestClient` in-process. Gateway-backed routes use deterministic mocks — no API key required. Checks schema compliance, required field presence, routing metadata, and context metadata. Not semantic evaluation.

**Reporting** — deterministic markdown report generator from telemetry and eval artifacts. Single-snapshot and before/after comparison modes.

**Auth middleware** — API key validation via `X-API-Key` header with constant-time comparison. Health endpoints exempt. Caller identity resolution seam for future multi-tenancy.

**Rate limiting** — per-caller sliding window backed by in-memory deque. Configurable via `RATE_LIMIT_RPM`.

## Extension points

The gateway invariant — all provider traffic through a single choke point — makes the following additions straightforward without changing route handlers:

- **Semantic cache** at the gateway layer (check before `ProviderBase.complete()`, store after).
- **Circuit breaker** wrapping the provider call to protect against cascading outage.
- **Provider fallback** via `ProviderBase`: if the primary provider errors, route to a secondary.
- **Multi-tenancy** by wiring `tenant_id` from auth middleware into the envelope and cost attribution.

These are architectural affordances, not implemented features.

## Adapting the routing classifier to your domain

The complexity classifier in `app/services/routing.py` uses intentionally generic keywords and length thresholds. This is the correct design for a reference implementation — the logic is deterministic, inspectable, and easy to replace.

In production, the keywords and thresholds are the variables that encode domain knowledge. They should be derived from real traffic analysis, not assumed upfront.

For an edtech platform, for example, the classifier might look like this:

```python
COMPLEX_KEYWORDS: tuple[str, ...] = (
    "step by step",
    "help me understand",
    "solve this problem",
    "walk me through",
    "essay",
    "explain in detail",
    "exam",
)

SIMPLE_KEYWORDS: tuple[str, ...] = (
    "what is",
    "definition of",
    "what does",
    "formula for",
    "meaning of",
)

SIMPLE_LENGTH_THRESHOLD = 60
COMPLEX_LENGTH_THRESHOLD = 180
```

The operational loop for calibrating these rules is:

1. Deploy with initial rules based on domain knowledge
2. Observe routing decisions in your OTel backend — which requests went to `expensive` and what was in them
3. Identify misrouted requests — false positives (cheap content sent to expensive tier) and false negatives (complex requests sent to cheap tier)
4. Refine keywords and thresholds
5. Measure cost impact in the before/after report

The telemetry infrastructure in this repository is what makes that loop possible and auditable. The classifier rules are where domain knowledge becomes measurable cost control.

## Routes

### `GET /healthz`
Liveness probe. Always 200.

### `GET /readyz`
Readiness probe. 200 when ready, 503 otherwise.

### `POST /classify-complexity`
Classifies request complexity and recommends a model tier locally, without an LLM call.

```bash
curl -X POST http://localhost:8000/classify-complexity \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_key" \
  -d '{"message": "What is 2+2?"}'
```

```json
{"complexity": "simple", "recommended_tier": "cheap", "needs_escalation": false}
```

### `POST /answer-routed`
Answer generation with model-tier routing. Returns selected model and routing decision.

```bash
curl -X POST http://localhost:8000/answer-routed \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_key" \
  -d '{"message": "Analyze the implications of quantum computing on cryptography"}'
```

```json
{"answer": "...", "selected_model": "gpt-4o", "routing_decision": "expensive"}
```

### `POST /conversation-turn`
Multi-turn conversation with explicit context strategy control.

```bash
curl -X POST http://localhost:8000/conversation-turn \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_key" \
  -d '{
    "conversation_id": "conv-123",
    "history": ["Hello", "How are you?"],
    "message": "Tell me about Python",
    "context_strategy": "sliding_window"
  }'
```

```json
{"answer": "...", "turn_index": 2, "context_tokens_used": 15, "context_strategy_applied": "sliding_window"}
```

## Project structure

```
llmscope/
├── src/llmscope/              ← pip-installable library
│   ├── __init__.py            ← public API surface
│   ├── py.typed               ← PEP 561 marker
│   ├── envelope.py            ← LLMRequestEnvelope contract
│   ├── semconv.py             ← llmscope.* attribute constants
│   └── gateway/
│       ├── client.py          ← call_llm() — the choke point
│       ├── provider.py        ← ProviderBase, OpenAI, Anthropic
│       ├── telemetry.py       ← emit() — OTel + JSONL dual-write
│       ├── cost_model.py      ← estimate_cost()
│       ├── policies.py        ← RoutePolicy per route
│       ├── otel_setup.py      ← setup_otel(), shutdown_otel()
│       └── semconv.py         ← gen_ai.* attribute constants
├── app/                       ← reference FastAPI app (not pip-installed)
├── evals/                     ← eval harness (not pip-installed)
├── reporting/                 ← report generator (not pip-installed)
└── tests/
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Required env vars:

```bash
export OPENAI_API_KEY="your_key_here"      # if using OpenAI
export ANTHROPIC_API_KEY="your_key_here"   # if using Anthropic
export APP_API_KEY="your_app_key_here"
```

Optional:

```
OTEL_EXPORTER_OTLP_ENDPOINT    OTLP collector endpoint (default: console exporter)
OTEL_SDK_DISABLED              set true to suppress OTel in CI
OTEL_SERVICE_NAME              default: llmscope
OTEL_SEMCONV_STABILITY_OPT_IN  GenAI convention migration opt-in
RATE_LIMIT_RPM                 default: 60
MAX_CONTEXT_TOKENS             default: 8192
```

## Running

```bash
uvicorn app.main:app --reload
```

Interactive docs at `/docs`.

## Tests

```bash
OTEL_SDK_DISABLED=true python3 -m pytest tests/ -q
```

## Linting and typing

```bash
python3 -m ruff check .
python3 -m ruff format --check .
python3 -m mypy src/llmscope/ app/ evals/ reporting/ --ignore-missing-imports
```

## Eval runners

```bash
OTEL_SDK_DISABLED=true python3 -m evals.runners.run_classify_eval
OTEL_SDK_DISABLED=true python3 -m evals.runners.run_answer_routed_eval
OTEL_SDK_DISABLED=true python3 -m evals.runners.run_conversation_turn_eval
```

Results written to `artifacts/reports/`.

## Reporting

Single snapshot:

```bash
python3 -m reporting.make_report \
  --after-log artifacts/logs/telemetry.jsonl \
  --output artifacts/reports/report.md
```

Before/after comparison:

```bash
python3 -m reporting.make_report \
  --before-log artifacts/logs/before_telemetry.jsonl \
  --after-log artifacts/logs/telemetry.jsonl \
  --output artifacts/reports/report.md
```

With eval results:

```bash
python3 -m reporting.make_report \
  --after-log artifacts/logs/telemetry.jsonl \
  --classify-eval artifacts/reports/classify_eval_results.json \
  --answer-eval artifacts/reports/answer_routed_eval_results.json \
  --conversation-eval artifacts/reports/conversation_turn_eval_results.json \
  --output artifacts/reports/report.md
```

## Telemetry event shape

Each gateway call serializes an `LLMRequestEnvelope` as one JSON line to `artifacts/logs/telemetry.jsonl`:

```json
{
  "schema_version": "0.1.0",
  "request_id": "46835b9b-90a0-4a06-83aa-999db8388c4e",
  "tenant_id": "default",
  "route": "/answer-routed",
  "provider_selected": "openai",
  "model_selected": "gpt-4o",
  "model_tier": "expensive",
  "routing_decision": "expensive",
  "tokens_in": 120,
  "tokens_out": 280,
  "tokens_total": 400,
  "estimated_cost_usd": 0.004,
  "cost_source": "estimated_local_snapshot",
  "latency_ms": 834.5,
  "status": "ok",
  "cache_hit": false
}
```

## Artifact paths

- `artifacts/logs/telemetry.jsonl`
- `artifacts/reports/classify_eval_results.json`
- `artifacts/reports/answer_routed_eval_results.json`
- `artifacts/reports/conversation_turn_eval_results.json`
- `artifacts/reports/report.md`

## Stability guarantees

LLMScope follows [Semantic Versioning](https://semver.org/). Starting from v0.1.0:

**Stable public API** — the following imports are stable and will not change without a major version bump:

```python
from llmscope import (
    LLMRequestEnvelope, GatewayResult, ProviderBase, ProviderResponse,
    call_llm, estimate_cost, setup_otel, shutdown_otel,
    OpenAIProvider, AnthropicProvider,
)
```

**Provider contract** — `ProviderBase` has two abstract methods (`provider_name`, `complete`) and two default methods (`is_retryable`, `categorize_error`). New optional methods with safe defaults may be added in minor versions. New abstract methods require a major version bump. Existing third-party provider subclasses will not break on minor upgrades.

**JSONL schema** — telemetry events include a `schema_version` field. New fields may appear in minor versions. Consumers should tolerate unknown keys (do not use `extra="forbid"` in Pydantic validators for telemetry events).

**OTel attribute namespace** — the `llmscope.*` attribute names defined in `src/llmscope/semconv.py` are stable from v0.1.0. They will not be renamed or removed without a major version bump. The `gen_ai.*` attributes follow the OpenTelemetry GenAI Semantic Conventions and may change according to the upstream spec via `OTEL_SEMCONV_STABILITY_OPT_IN`.

**Cost model** — model pricing values are configuration, not API. They may be updated in any version to reflect current provider pricing. The `estimate_cost()` function signature is stable.

## What this is not

Not a general-purpose agent framework, a notebook-based experiment, a semantic evaluation suite, a dashboard product, or a production SaaS system. It is a small, inspectable engineering artifact that shows how to build the core of an LLM control plane with explicit gateway boundaries, a provider-agnostic abstraction, cost-aware routing visibility, OpenTelemetry instrumentation, a versioned envelope contract, local run artifacts for offline analysis, regression discipline, and deterministic reporting.

## Related projects

- [llm-eval-gate](https://github.com/lucianareynaud/llm-eval-gate) — Evidence-based quality gate that consumes LLMScope telemetry to produce go/no-go deployment decisions.

## Tooling

Specs under `.kiro/` were authored using Kiro for structured design documentation. Architecture decisions, instrumentation boundaries, and semantic convention alignment reflect production experience with OpenTelemetry GenAI conventions and cost attribution in regulated environments.
