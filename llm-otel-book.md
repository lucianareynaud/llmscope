# OpenTelemetry for LLM Production Systems

**Observability · FinOps · Governance · Reliability**

> A technical field guide grounded in the [llm-otel](../README.md) reference architecture.
> Intended audience: senior AI systems engineers instrumenting LLMs in production.

---

## Table of Contents

1. [The Observability Problem in Production AI](#1-the-observability-problem-in-production-ai)
2. [OpenTelemetry Architecture: API, SDK, and the Proxy Pattern](#2-opentelemetry-architecture-api-sdk-and-the-proxy-pattern)
3. [Semantic Conventions: The Contract You Don't Own](#3-semantic-conventions-the-contract-you-dont-own)
4. [Tracing LLM Calls: Spans, Context, and Cardinality](#4-tracing-llm-calls-spans-context-and-cardinality)
5. [Metrics Design for LLM Systems](#5-metrics-design-for-llm-systems)
6. [LLM FinOps: Cost Attribution, Estimation, and the Reporting Pipeline](#6-llm-finops-cost-attribution-estimation-and-the-reporting-pipeline)
7. [Model Routing, Policy Enforcement, and the Gateway Pattern](#7-model-routing-policy-enforcement-and-the-gateway-pattern)
8. [Testing Instrumented Systems: The Mock Trap and How to Escape It](#8-testing-instrumented-systems-the-mock-trap-and-how-to-escape-it)
9. [Production Deployment: Sampling, Collectors, and Operational Trade-offs](#9-production-deployment-sampling-collectors-and-operational-trade-offs)
10. [Critical Assessment of llm-otel: Strengths, Gaps, and the Roadmap](#10-critical-assessment-of-llm-otel-strengths-gaps-and-the-roadmap)
11. [Field Reference: Environment Variables, Patterns, and Anti-patterns](#11-field-reference-environment-variables-patterns-and-anti-patterns)

---

## 1. The Observability Problem in Production AI

Traditional application observability assumes a deterministic system: the same input reliably produces the same output, latency is bounded by infrastructure, and errors are discrete, classifiable events. LLMs break all three assumptions simultaneously, and the instrumentation discipline built around the first generation of microservices does not translate.

A single inference call on a production LLM involves at least four sources of non-determinism: the model's stochastic sampling process, variable token count depending on prompt and response content, provider-side rate limiting and backoff, and network jitter to a remote API endpoint that you do not control. The cost of that call is not fixed — it is proportional to actual token consumption, which can vary by an order of magnitude between requests on the same route.

### Why Traditional APM Fails

First-generation APM tools are optimized for the web request/response cycle: fixed schema, bounded latency, HTTP status codes as the primary error signal. None of these assumptions hold for LLM calls:

- **HTTP 200 is not success.** An LLM call that returns 200 with a hallucinated or schema-invalid response is a failure by any business definition. The status code tells you nothing about output quality.
- **Latency is bimodal.** Cache hits return in single-digit milliseconds. Cache misses on a large model return in seconds. Averaging these produces a metric that accurately describes neither population.
- **Cost is invisible in APM.** Standard APM has no concept of per-request compute cost borne by the application owner, not the infrastructure. Token-based pricing requires a cost attribution layer that classical APM was never designed to provide.
- **Error taxonomy is coarser.** A rate limit (429), a context length exceeded (400), an authentication failure (401), and a model-side internal error (500) are all HTTP errors — but each requires a completely different operational response.

### The Three Pillars and Their LLM Semantics

#### Traces

A trace is a causally linked set of spans representing the execution of a single request across one or more processes. The critical design decision is span granulality: **one span per LLM call, not one span per retry attempt.** The span lifecycle for an LLM call:

- **START:** set request attributes — model name, max tokens, route, tier, retry budget
- **SUCCESS:** add usage attributes — tokens_in, tokens_out, cost, latency — and leave status `UNSET` (OTel convention for no error)
- **ERROR:** call `span.record_exception()` to attach the full stack trace as a span event, set `StatusCode.ERROR`, add `error.type` and `error_category` attributes

The [`gateway/client.py`](../gateway/client.py) module implements this pattern precisely: one `CLIENT` span wraps the entire `_call_provider()` retry loop. Each retry is not a new child span — it is an event on the parent span.

> **Architecture note:** `SpanKind.CLIENT` is the correct kind for LLM calls. The OTel spec defines CLIENT spans as outbound remote calls. The HTTP request span created by `FastAPIInstrumentor` is `SERVER` kind. This produces the canonical trace structure: `HTTP POST /answer-routed → chat gpt-4o-mini`.

#### Metrics

Four instruments cover the Pareto set of production observability needs for LLM systems:

| Instrument | What it measures |
|---|---|
| `gen_ai.client.token.usage` (Histogram) | Token counts per call, split by `gen_ai.token.type` (input/output). Histograms expose distribution — critical because LLM token counts are heavy-tailed. |
| `gen_ai.client.operation.duration` (Histogram) | Wall-clock latency in seconds. Includes retry wait time, making it the true SLO-relevant duration. |
| `llm_gateway.estimated_cost_usd` (Counter) | Cumulative USD cost. A Counter is correct because cost is monotonically increasing. |
| `llm_gateway.requests` (Counter) | Request volume by route, tier, and status. Enables error rate: `errors / (success + errors)`. |

#### Logs (Structured Events)

In llm-otel, structured logging is implemented as a JSONL file (`artifacts/logs/telemetry.jsonl`) rather than as OTel log records. This is a deliberate trade-off: the JSONL file is backend-agnostic, readable by `jq` and `pandas` without a running collector, and serves the offline reporting pipeline. The OTel metrics layer handles live observability; the JSONL layer handles batch reporting.

This dual-write pattern — one OTel sink for live dashboards, one JSONL sink for batch analysis — acknowledges that in early production stages you often need both a real-time view and an exportable artifact that does not depend on a running observability backend.

### Why OpenTelemetry Won

The vendor lock-in problem in observability was severe before OpenTelemetry. Instrumenting for Datadog meant Datadog-specific SDKs. OpenTelemetry solved this with a two-layer architecture: instrument once against the OTel API, configure the backend via the SDK and exporters. The same instrumentation code emits to Grafana Cloud, Datadog, Honeycomb, New Relic, or a self-hosted collector by changing environment variables.

For LLM systems specifically, this matters because the observability backend market for AI is still consolidating. Locking to any single vendor at the instrumentation layer is a poor bet. OpenTelemetry's backend-agnostic OTLP protocol is the correct hedge.

---

## 2. OpenTelemetry Architecture: API, SDK, and the Proxy Pattern

OpenTelemetry's most consequential design decision is the strict separation of API from SDK. Understanding this separation is prerequisite knowledge for correctly instrumenting any system intended to be packaged as a reusable library.

### The API/SDK Separation

The **OTel API** is a thin, dependency-light contract layer. It defines the interfaces — `Tracer`, `Meter`, `SpanContext`, `Instrument` — but provides no implementation beyond no-op stubs. Library code that wants to emit telemetry depends only on the API, without imposing a particular SDK or backend on consumers.

The **OTel SDK** is the implementation layer. It provides: `TracerProvider` and `MeterProvider` implementations backed by real span processors and exporters, the `BatchSpanProcessor` and `PeriodicExportingMetricReader` that handle the actual export pipeline, the `Resource` system for attaching service identity to telemetry, and the exporter implementations (OTLP, Console, Zipkin, etc.).

Application code — not library code — is responsible for configuring and registering the SDK. The application calls `set_tracer_provider()` and `set_meter_provider()` once at startup, and from that point any call to `trace.get_tracer()` or `metrics.get_meter()` anywhere in the process returns an instrument backed by the real SDK.

> **llm-otel reference:** This is why [`gateway/otel_setup.py`](../gateway/otel_setup.py) exists as a separate module. It is the single place in the entire application that touches the SDK. Everything else — `telemetry.py`, `client.py` — touches only the API. If you wanted to swap the SDK implementation, you would change only `otel_setup.py`.

### The Proxy Pattern: Safe Module-Level Instrument Creation

Before `setup_otel()` is called, `trace.get_tracer()` returns a `ProxyTracer` — not a no-op. A `ProxyTracer` buffers method calls internally. When `set_tracer_provider()` is called, the `ProxyTracer` transparently upgrades to the real SDK-backed tracer.

This means the following pattern — used throughout llm-otel — is safe:

```python
# gateway/client.py — module-level, runs at import time
# Before setup_otel() runs, this returns a ProxyTracer.
# After setup_otel() runs, it upgrades to the real tracer.
_tracer = trace.get_tracer(__name__, tracer_provider=None)

# gateway/telemetry.py — same pattern for metrics
_meter = metrics.get_meter("llm_gateway", version="0.1.0")
_token_usage_histogram = _meter.create_histogram(
    name=METRIC_TOKEN_USAGE,
    unit="{token}",
)
```

If the proxy pattern did not exist, every module that creates instruments would need to guard against being imported before `setup_otel()` runs. The proxy pattern eliminates this entire class of initialization ordering bugs.

### Provider Hierarchy

```
Resource  (service.name, service.version, deployment.environment)
  ├── TracerProvider
  │     └── BatchSpanProcessor
  │           └── OTLPSpanExporter   (if OTEL_EXPORTER_OTLP_ENDPOINT is set)
  │               or ConsoleSpanExporter  (local dev fallback)
  └── MeterProvider
        └── PeriodicExportingMetricReader
              └── OTLPMetricExporter  (if OTEL_EXPORTER_OTLP_ENDPOINT is set)
                  or ConsoleMetricExporter  (local dev fallback)
```

### BatchSpanProcessor vs SimpleSpanProcessor

`SimpleSpanProcessor` exports each span synchronously in the request hot path: the HTTP response does not return until the span has been exported to the backend. Under any meaningful load, this creates a latency coupling between your application and your observability backend.

`BatchSpanProcessor` maintains an in-memory queue. A background thread reads from this queue and exports spans in batches, completely decoupled from the request path. The trade-off: spans in the queue at process death are lost unless `shutdown()` is called.

> **Production failure mode:** If you forget to call `shutdown_otel()` on application exit, the last N seconds of spans held in the `BatchSpanProcessor` queue are silently discarded. The correct pattern: lifespan handler `yield`-based shutdown guarantees cleanup before the process exits. See [`app/main.py`](../app/main.py).

### The OTLP Protocol and Backend Agnosticism

OTLP (OpenTelemetry Protocol) is the wire format that replaced the fragmented landscape of Jaeger, Zipkin, and vendor-specific protocols. It is available over gRPC (port 4317) and HTTP (port 4318). llm-otel uses HTTP/protobuf to avoid the heavy `grpcio` dependency.

The same running llm-otel instance, without code changes, can emit to Grafana Cloud, Datadog, Honeycomb, New Relic, or a self-hosted OpenTelemetry Collector — by changing only `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS`.

---

## 3. Semantic Conventions: The Contract You Don't Own

Semantic conventions are the shared vocabulary of OpenTelemetry. They define the attribute names, value enumerations, and metric instrument names that observability backends use to parse and display telemetry. A span with `span.name = "call_llm"` and an attribute named `my.tokens` tells a backend nothing. A span named `"chat gpt-4o-mini"` with `gen_ai.usage.input_tokens` tells Grafana, Datadog, and Honeycomb exactly what they need to render a cost dashboard.

### The Stability Spectrum

| Stability Level | Meaning |
|---|---|
| **Stable** | Production-ready. No breaking changes without a major version bump. Safe to build dashboards and alerts on. |
| **Experimental (Incubating)** | May change. New attributes can be added; existing attributes can be renamed or removed with a deprecation cycle. |
| **Development** | Active development. Can change at any time without deprecation. **The current GenAI conventions live here.** |

The underscore in `_incubating` is the Python convention for "do not import this directly from library code." When the OTel team renames an attribute in the Development tier, they may not bump the package version. Your code breaks silently — cost dashboards go dark, alerts stop firing.

### The Current GenAI Migration: `gen_ai.system` → `gen_ai.provider.name`

The legacy attribute `gen_ai.system` (introduced in v1.36.0) is being replaced by `gen_ai.provider.name` per the latest OpenAI semconv specification. This creates a migration problem:

- Backends built on `gen_ai.system` break if you switch to `gen_ai.provider.name` only.
- Emitting only `gen_ai.system` makes you non-compliant with the latest experimental spec.
- The OTel-recommended solution is dual-emission during a migration window.

llm-otel solves this with `OTEL_SEMCONV_STABILITY_OPT_IN` and the `resolve_attrs()` function in [`gateway/semconv.py`](../gateway/semconv.py):

```python
# OTEL_SEMCONV_STABILITY_OPT_IN modes for GenAI:
#
# (not set)                       → emit gen_ai.system only (v1.36.0 compatible)
# gen_ai_latest_experimental      → emit gen_ai.provider.name only (latest spec)
# gen_ai_latest_experimental/dup  → emit BOTH (migration window)
```

### The `semconv.py` Pattern: Single Source of Truth

Every attribute name string and every metric name string in the entire gateway is defined exactly once, in [`gateway/semconv.py`](../gateway/semconv.py). No other module imports from `opentelemetry.semconv` directly.

The consequence: when the OTel team renames `gen_ai.system` to `gen_ai.provider.name`, the change is made in one place and propagated automatically to every span and metric emitted by the gateway. This is an anti-corruption layer between your codebase and a contract you do not control.

```python
# gateway/semconv.py — the only file that names attribute strings
ATTR_GEN_AI_SYSTEM        = "gen_ai.system"        # v1.36.0 legacy
ATTR_GEN_AI_PROVIDER_NAME = "gen_ai.provider.name" # latest experimental
VAL_GEN_AI_SYSTEM_OPENAI  = "openai"

_PENDING_RENAMES: dict[str, str] = {
    ATTR_GEN_AI_SYSTEM: ATTR_GEN_AI_PROVIDER_NAME,
}

def resolve_attrs(attrs: dict) -> dict:
    # Apply dual-emission or replacement based on OTEL_SEMCONV_STABILITY_OPT_IN.
    # Pure function — no side effects, safe to call from anywhere.
    ...
```

### What Every GenAI Span Must Have

Per the current OTel GenAI Semantic Convention specification, a conformant LLM call span must carry:

- `gen_ai.system` or `gen_ai.provider.name` — the provider identifier
- `gen_ai.operation.name` — e.g. `"chat"` for completion calls
- `gen_ai.request.model` — the model ID as sent in the request
- `gen_ai.usage.input_tokens` — input token count from the response
- `gen_ai.usage.output_tokens` — output token count from the response

The span name format is mandated: `"{gen_ai.operation.name} {gen_ai.request.model}"`, e.g. `"chat gpt-4o-mini"`. Backends that support GenAI semconv natively parse this to render cost waterfall views and model comparison panels automatically — but only if the attribute names exactly match the convention.

---

## 4. Tracing LLM Calls: Spans, Context, and Cardinality

### Span Structure for LLM Operations

The canonical trace for a single gateway-routed LLM call has two spans:

```
HTTP POST /answer-routed  [kind=SERVER, created by FastAPIInstrumentor]
  └── chat gpt-4o-mini    [kind=CLIENT, created by gateway/client.py]
```

The `SERVER` span is created automatically by `FastAPIInstrumentor`. The `CLIENT` span is created manually by `call_llm()` as a child of the `SERVER` span. `SpanKind.CLIENT` is correct because this process is acting as a client to an external service. Kind is not cosmetic — backends use it to compute RED metrics and to render trace views.

### Attribute Cardinality: The Silent Budget

Every attribute added to a metric recording becomes a dimension in the backend's storage system. High-cardinality attributes — those with many unique values — can exhaust backend storage and break ingestion pipelines.

**Cardinal rule: never put `request_id`, `user_id`, `session_id`, or any identifier that grows unboundedly as a metric attribute.**

| Attribute | Cardinality | Correct placement |
|---|---|---|
| `gen_ai.request.model` | 2–10 | Metric attribute ✓ |
| `llm_gateway.route` | 2–5 | Metric attribute ✓ |
| `status` | 2 | Metric attribute ✓ |
| `error.type` (well-enumerated) | 5–10 | Metric attribute ✓ |
| `request_id` | Unbounded | Span attribute only |
| `conversation_id` | Unbounded | Span attribute only |
| Prompt content | Unbounded + PII | Never |

llm-otel's `_record_otel_metrics()` uses four low-cardinality dimensions: route (2 values), model (2 values), status (2 values), error_type (6 categorized values). See [`gateway/telemetry.py`](../gateway/telemetry.py).

### The Retry Instrumentation Decision

llm-otel models retry attempts as events on a single span, not as child spans. One LLM call = one span in the UI, always. Failed intermediate attempts are captured as span events via `record_exception()`. The final outcome (success or error after all retries) is what the span status reflects.

This maps cleanly to SLO definitions: the user experienced one LLM operation, which either succeeded or failed.

```python
# gateway/client.py — retry loop
for attempt in range(retry_attempts + 1):
    try:
        text, tokens_in, tokens_out = provider_call()
        return text, tokens_in, tokens_out  # span ends as SUCCESS
    except Exception as exc:
        if _is_retryable(exc) and attempt < retry_attempts:
            # Not a new child span — just backoff and retry
            time.sleep(2 ** attempt)
            continue
        break  # span ends as ERROR on the last attempt
```

### `record_exception()` vs `set_status()`: Both Are Required

- `span.record_exception(exc)` creates a structured span event named `"exception"` containing the exception type, message, and full stack trace. This is what backends render as the error event on the span timeline.
- `span.set_status(Status(StatusCode.ERROR, description))` marks the span itself as failed. This is what backends use to calculate error rates and trigger error alerts.

Without `record_exception()`: no stack trace. Without `set_status()`: the span is counted as success in error-rate aggregations even though it has a recorded exception.

```python
except Exception as exc:
    span.record_exception(exc)                              # captures stack trace as span event
    span.set_status(Status(StatusCode.ERROR, str(exc)))     # marks span as failed
    span.set_attribute("error.type", type(exc).__name__)    # OTel error attribute
    span.set_attribute("llm_gateway.error_category", error_type)  # internal taxonomy
    raise
```

### `UNSET` vs `OK` Status

On successful spans, llm-otel deliberately does not call `span.set_status(Status(StatusCode.OK))`. `StatusCode.UNSET` means "no error was detected by this instrumentation." `StatusCode.OK` means "the application explicitly asserts this operation was successful." For library-level CLIENT spans, `UNSET` is correct — backends treat it as success in error-rate calculations.

---

## 5. Metrics Design for LLM Systems

### Instrument Types and Their Semantics

**Counter** — monotonically increasing. Can only go up. Correct for: request volume, error count, cumulative cost. Wrong for: latency, queue depth, any value that can decrease. llm-otel uses Counters for `llm_gateway.requests` and `llm_gateway.estimated_cost_usd`.

**Histogram** — records the distribution of a value, not just its average. Correct for latency and token counts because:
- LLM latency is bimodal (cache hits vs. misses). Averaging them produces a metric that describes neither.
- Token counts are heavy-tailed. The average hides the tail behavior that drives cost overruns.
- Percentiles (p50, p95, p99) are derivable from Histogram data. SLO definitions are expressed in percentiles.

The token usage histogram is recorded **twice** per call — once for input tokens with `gen_ai.token.type=input`, once for output tokens with `gen_ai.token.type=output`. This is mandated by the OTel GenAI spec because input and output tokens have different price coefficients.

**Gauge** — a value that can go up and down. Correct for: current queue depth, rate limit budget remaining. Not used in the current llm-otel implementation but appropriate for future extensions that track provider rate limit headroom.

### Push vs Pull: OTLP vs Prometheus

**Push (OTLP):** The application exports metrics to a receiver on a fixed interval (`PeriodicExportingMetricReader`, default 30 seconds). The application knows where the backend is. This is the model used by llm-otel.

**Pull (Prometheus):** A Prometheus server scrapes the application's `/metrics` endpoint on its own schedule. Dominant in Kubernetes environments because Prometheus service discovery handles collection topology.

OTel supports both: `PrometheusExporter` exposes a `/metrics` scrape endpoint from OTel instrument data. For llm-otel deployed in Kubernetes with an existing Prometheus stack, adding `PrometheusExporter` alongside `OTLPMetricExporter` supports both collection models simultaneously.

### Export Interval Tuning

| Interval | Trade-off |
|---|---|
| 30s (default) | 30s lag before events appear in dashboards. Acceptable for trend analysis, too slow for incident response. |
| 10s (production recommendation) | Fast enough to catch a cost spike within seconds. Appropriate for active alerting. |
| 1s | Real-time, but generates significant write volume. Only for high-stakes scenarios. |

The export interval and the `BatchSpanProcessor` flush interval are independent settings and should be tuned separately.

---

## 6. LLM FinOps: Cost Attribution, Estimation, and the Reporting Pipeline

### The Token as the Unit of Cost

Every LLM API call is billed on a per-token basis, with different rates for input and output tokens, and different rates per model. Two identical HTTP 200 responses from the same endpoint can differ in cost by 100x depending on what was in the prompt and what the model generated.

Observability for LLM cost must happen at the application layer, not at the infrastructure layer. A network proxy sees bytes, not tokens. Only the application — which has access to the structured response from the LLM API — can extract the token counts that drive cost calculation. See [`gateway/cost_model.py`](../gateway/cost_model.py).

```python
MODEL_PRICING = {
    "gpt-4o-mini": { "input_per_1m": 0.15, "output_per_1m": 0.60 },
    "gpt-4o":      { "input_per_1m": 2.50, "output_per_1m": 10.00 },
}

def estimate_cost(model, tokens_in, tokens_out) -> float:
    # cost = (tokens_in / 1_000_000) * input_price
    #      + (tokens_out / 1_000_000) * output_price
```

### Cost Attribution: From Call to Business Unit

The `metadata` dict passed to `call_llm()` propagates into both the OTel span and the JSONL event:

```python
result = call_llm(
    prompt=prepared_prompt,
    model_tier="cheap",
    route_name="/answer-routed",
    metadata={
        "routing_decision": "cheap",
        "feature": "document-qa",       # low cardinality: OK as metric attribute
        "cost_center": "product-team-a", # low cardinality: OK as metric attribute
        # customer_id: NOT a metric attribute — put only in span and JSONL
    }
)
```

The critical discipline: separate metadata that goes into metric attributes (low cardinality: feature, cost_center, tier) from metadata that goes only into span attributes and JSONL events (high cardinality: customer_id, conversation_id).

### The Dual-Write Pattern

llm-otel implements deliberate dual-write telemetry. This is not redundancy — it is functional separation:

- **OTel metrics pipeline** answers live questions: what is the current error rate? Is cost increasing over the last 5 minutes? Is the p95 latency SLO being met?
- **JSONL pipeline** answers batch questions: what was the total cost per customer last month? Which prompt patterns consume the most tokens?

```python
# Querying telemetry.jsonl with pandas for cost-per-route analysis
import pandas as pd

df = pd.read_json("artifacts/logs/telemetry.jsonl", lines=True)
df["timestamp"] = pd.to_datetime(df["timestamp"])

cost_by_route  = df.groupby("route")["estimated_cost_usd"].sum()
p95_latency    = df.groupby("model")["latency_ms"].quantile(0.95)
df["token_ratio"] = df["tokens_out"] / df["tokens_in"].replace(0, 1)
efficiency     = df.groupby("route")["token_ratio"].mean()
```

### Pricing Snapshot: The Honest Limitation

llm-otel's cost estimates are based on a hardcoded pricing snapshot documented with a retrieval date. This is explicitly noted as "not a billing source of truth." Use `estimated_cost_usd` for alerting on anomalies and trends, not for financial reporting. For financial reporting, reconcile against provider invoices. The gap between estimated and actual cost should itself be tracked as a calibration metric.

---

## 7. Model Routing, Policy Enforcement, and the Gateway Pattern

### Why a Centralized Gateway

Without a single choke point through which all LLM calls flow, you get: no consistent retry policy, no centralized cost attribution, no uniform span structure, no ability to swap models without modifying every route handler, and no circuit breaking.

In llm-otel, the rule is absolute: **no route or service imports `openai` directly.** All LLM interactions go through `call_llm()` in [`gateway/client.py`](../gateway/client.py). In a larger team, enforce this with a linting rule or by making `openai` a gateway-only dependency.

### The `RoutePolicy` Pattern

[`gateway/policies.py`](../gateway/policies.py) defines `RoutePolicy` frozen dataclasses that capture all configuration relevant to a route's LLM behavior:

```python
@dataclass(frozen=True)
class RoutePolicy:
    max_output_tokens: int   # budget cap per call
    retry_attempts: int      # how many retries on transient failures
    cache_enabled: bool      # whether semantic cache applies
    model_for_tier: dict     # {"cheap": "gpt-4o-mini", "expensive": "gpt-4o"}
```

Frozen dataclasses are correct here: policies are configuration, not runtime state. Making them immutable prevents the failure mode where a route handler accidentally modifies shared policy state.

### Complexity-Based Routing

The `/answer-routed` endpoint implements complexity-based model routing: a `classify_complexity()` call determines whether the query is simple (cheap tier: `gpt-4o-mini`) or complex (expensive tier: `gpt-4o`).

The instrumentation of the routing decision is as important as the routing itself. Every call includes `routing_decision` in metadata, flowing into both the OTel span and JSONL event. Without this instrumentation, you have a routing system you cannot measure, cannot calibrate, and cannot justify to finance.

### Retryable vs Non-Retryable Errors

[`gateway/client.py`](../gateway/client.py)'s `_is_retryable()` uses `isinstance()` checks against the typed OpenAI exception hierarchy:

- Exception types are stable across SDK versions; message text is not.
- `isinstance()` correctly handles subclass relationships (`APITimeoutError` is a subclass of `APIConnectionError` — must be checked first).

```python
# Retryable: transient, may succeed on retry
openai.RateLimitError      # 429
openai.APITimeoutError     # network timeout
openai.APIConnectionError  # network failure
openai.InternalServerError # 5xx provider-side

# Non-retryable: structural, retrying wastes money
openai.AuthenticationError      # 401
openai.BadRequestError          # 400
openai.UnprocessableEntityError # 422 (context length exceeded)
```

---

## 8. Testing Instrumented Systems: The Mock Trap and How to Escape It

### `OTEL_SDK_DISABLED`: The Correct Test Isolation Pattern

For unit tests that don't care about telemetry output:

```python
# conftest.py
@pytest.fixture(autouse=True)
def disable_otel(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
```

For tests that need to verify what telemetry was emitted, use `InMemorySpanExporter`:

```python
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

@pytest.fixture
def span_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()

def test_span_has_cost_attribute(span_exporter, mock_openai):
    call_llm("hello", "cheap", "/answer-routed")
    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["llm_gateway.estimated_cost_usd"] > 0
```

### The Mock Trap: What the Eval Harness Tests vs. What It Doesn't

The current eval harness mocks all LLM calls at the client level. This correctly tests structural behavior: does the gateway emit correct spans? Does the routing logic select the right tier? Does the retry loop handle exceptions correctly?

**What it does not test:** whether the actual model produces responses that meet quality criteria. The eval datasets contain real-world test inputs and expected outputs, but the mock layer never sends them to a model. This is the critical gap documented in `spec-014-eval-layers`:

> The eval harness needs two additional layers: **Promptfoo** (Camada 2) for model-in-the-loop evaluation, and **Langfuse via OTLP** (Camada 3) for production trace-based evaluation.

Without Camada 2, the system cannot detect model quality regressions. Without Camada 3, it cannot connect observability data to quality outcomes.

### CI Security Risk: API Keys and `pull_request` Events

If future CI configuration adds live eval workflows — tests that make real API calls — on `pull_request` events with API keys in the environment, a security vulnerability arises: a pull request from a forked repository can exfiltrate the API key.

**Correct CI architecture for live evals:**
- Keep mock-based unit tests on `pull_request` (no secrets needed, safe)
- Run live eval workflows only on `push` to `main` (post-merge, not on PRs from forks)
- Never pass API keys to `pull_request` workflows that could be triggered by external contributors

---

## 9. Production Deployment: Sampling, Collectors, and Operational Trade-offs

### Sampling Strategy

**Head-based sampling** (decision made at trace start): simple, zero hot-path overhead, but may drop a trace that turns out to be interesting (e.g., it becomes an error after the sampling decision was made).

**Tail-based sampling** (decision made after the trace completes): requires the OpenTelemetry Collector. Allows rules like "always keep error traces" and "always keep high-latency traces" — exactly the ones you need for LLM production debugging.

```yaml
# otel-collector-config.yaml — tail-based sampling
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: keep-errors
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: keep-high-latency
        type: latency
        latency: { threshold_ms: 5000 }
      - name: sample-rest
        type: probabilistic
        probabilistic: { sampling_percentage: 10 }
```

### The OpenTelemetry Collector: When to Use It

llm-otel sends telemetry directly to the backend (direct OTLP export) — correct for development and MVP. In production at scale, a Collector adds value:

- **Fan-out:** receive from the application, export to multiple backends simultaneously without code changes.
- **Tail-based sampling:** only possible through the Collector.
- **Attribute filtering:** remove PII or high-cardinality attributes before they reach the backend.
- **Batching and compression:** protect the application from backend slowness.
- **Centralized auth:** manage credentials in one place rather than distributing API keys to every service.

Decision threshold: if you have more than one service emitting telemetry, a Collector is worth the operational overhead.

### Resource Attributes for Multi-Service Environments

```python
# Minimum viable resource for production multi-service deployment
Resource.create({
    SERVICE_NAME: "llm-cost-control-gateway",
    SERVICE_VERSION: "1.2.0",
    DEPLOYMENT_ENVIRONMENT: "production",
    "service.namespace": "llm-platform",       # groups related services
    "service.instance.id": pod_name,            # Kubernetes pod name
    "k8s.cluster.name": "prod-us-east-1",
    "k8s.namespace.name": "ai-platform",
})
```

In Kubernetes, the OpenTelemetry Operator can inject resource attributes from the pod environment automatically via `OTEL_RESOURCE_ATTRIBUTES`, so application code does not need to know its own pod name or cluster name.

### Export Reliability and Back-Pressure

The `BatchSpanProcessor` has a bounded queue (default: 2048 spans). If spans are produced faster than they can be exported — backend slow or unreachable — the queue fills and new spans are dropped. This is the correct behavior: telemetry should never block or slow the application.

If the OTel backend is unavailable for extended periods, a local Collector with a persistent queue provides durable telemetry delivery: it buffers locally and drains when the backend recovers.

---

## 10. Critical Assessment of llm-otel: Strengths, Gaps, and the Roadmap

### What llm-otel Gets Right

**The `semconv.py` isolation pattern** is the most architecturally mature decision in the codebase. Centralizing all attribute name strings in a single module, using a local `resolve_attrs()` for migration management, and never importing from `opentelemetry.semconv._incubating` in application code is exactly the correct posture for Development-stability conventions. This pattern should be a standard recommendation for any team instrumenting LLM systems.

**Cardinality discipline:** `_record_otel_metrics()` explicitly documents which attributes are metric-safe and why high-cardinality identifiers must never appear as metric attributes. The implementation correctly uses four dimensions with bounded values.

**The dual-write architecture** acknowledges that not every team has a running observability backend, and not every question is best answered by a live query. The JSONL pipeline enables reporting without requiring Grafana to be running.

**Retry instrumentation design:** one span per logical operation, retry failures as span events. Consistent with OTel GenAI spec intention.

**Transparent cost estimation with honest limitations:** documenting the pricing snapshot date and explicitly stating "not a billing source of truth" sets correct expectations.

### What llm-otel Still Needs

**Eval harness Camada 2 and Camada 3:** the current harness tests structural behavior with mocked responses. It does not test model quality. A model provider update that degrades output quality will not surface until users report it.

**Structured logging layer (OTel-native):** the current JSONL emission is not OTel log records. The `fcntl` locking on the JSONL file is also a potential bottleneck under high concurrency — multiple uvicorn workers competing for a file lock will serialize on every request.

**Semantic cache instrumentation:** `cache_hit` is present in `GatewayResult` and JSONL events but is not a first-class metric dimension. Cache hit rate is one of the highest-leverage cost control metrics — a 30% cache hit rate translates directly to 30% cost reduction.

**Circuit breaker state instrumentation:** spec 011 exists but circuit breaker state transitions (`CLOSED → OPEN → HALF_OPEN`) are not instrumented. State transitions are a critical operational signal that neither error rates nor latency histograms alone capture.

**Provider abstraction for multi-provider routing:** the current gateway is OpenAI-specific. A `Provider` interface with concrete implementations per provider would make multi-provider routing possible without changing the gateway's core logic or its instrumentation.

### The Roadmap to Vendable Standard

1. **Published semconv compliance report** — a document mapping every emitted attribute to the specific semconv version it implements, with explicit notation of stable vs. experimental.
2. **Promptfoo integration (Camada 2)** — model-in-the-loop eval for the three existing routes, with baseline scores and pass/fail thresholds in CI.
3. **Multi-provider abstraction** — a `Provider` interface with at least Anthropic and OpenAI implementations.
4. **OpenTelemetry Collector example configuration** — a `docker-compose` or Helm chart demonstrating the full stack: `llm-otel → Collector → Grafana`, with pre-built dashboards.
5. **Benchmark with real traffic** — `scripts/benchmark_before_after.py` needs a published baseline: cost and latency with and without the routing layer, using production-representative traffic.

The `semconv.py` pattern, the cardinality discipline, and the dual-write architecture already differentiate llm-otel from generic LLM monitoring tutorials. The missing pieces above are the difference between "a well-instrumented gateway" and "the reference implementation that enterprise teams adopt."

---

## 11. Field Reference: Environment Variables, Patterns, and Anti-patterns

### Environment Variable Reference

| Variable | Effect and recommended value |
|---|---|
| `OTEL_SERVICE_NAME` | Service identity in every span and metric. Use a consistent, human-readable name: `llm-cost-control-gateway`. Never use generic names like `app`. |
| `OTEL_SERVICE_VERSION` | Enables release-based analysis. Set to the deployed artifact version using semver: `1.2.0`. |
| `OTEL_DEPLOYMENT_ENVIRONMENT` | Separates prod/staging/dev in backend queries. Values: `production`, `staging`, `development`. Never omit in production. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | The OTLP receiver base URL. HTTP: `http://collector:4318`. gRPC: `http://collector:4317`. If unset, llm-otel falls back to `ConsoleExporter`. |
| `OTEL_EXPORTER_OTLP_HEADERS` | Auth headers for managed backends. Format: `"Authorization=Bearer <token>"`. Rotatable without code changes. |
| `OTEL_METRIC_EXPORT_INTERVAL` | `PeriodicExportingMetricReader` interval in ms. Default: `30000`. Production recommendation: `10000` for cost alerting. |
| `OTEL_SDK_DISABLED` | Set `"true"` to silence all OTel (no-op providers). Use in unit tests. Never in production. |
| `OTEL_SEMCONV_STABILITY_OPT_IN` | GenAI migration mode. Use `gen_ai_latest_experimental/dup` during migration windows for dual-emission. |

### The Ten Patterns Every OTel Engineer Should Know

1. **Instrument once, configure via environment.** No backend-specific code in application modules. The same binary deploys to dev, staging, and production by changing environment variables only.

2. **Provider setup at startup, not at import.** `setup_otel()` called once from the lifespan handler. Instruments created at module level use the proxy pattern and work before and after setup.

3. **Shutdown explicitly.** Always call `shutdown_otel()` in the lifespan cleanup. Without it, the last N seconds of spans and the last metric export cycle are silently lost.

4. **Single semconv module.** All attribute name strings defined once in a local semconv module. No direct imports from `opentelemetry.semconv._incubating` in application code.

5. **Cardinality budget.** Metrics: maximum 5 dimensions per instrument, each with cardinality < 20. Spans: unlimited within reason. Never put `request_id`, `user_id`, or `session_id` as metric attributes.

6. **One span per LLM operation, not per retry.** The span represents the logical operation from the caller's perspective. Retry failures are span events, not child spans.

7. **`CLIENT` kind for outbound LLM calls.** `SpanKind.CLIENT` for all LLM provider calls. `SpanKind.SERVER` for inbound HTTP request spans. Kind affects backend metric calculations and trace rendering.

8. **`UNSET` status on success, not `OK`.** Library and gateway code leaves status `UNSET` on success. `OK` is reserved for application-level assertions of successful business outcomes.

9. **`record_exception()` AND `set_status()` on error.** Both are needed. `record_exception()` captures the stack trace as a span event. `set_status()` marks the span as failed in aggregation.

10. **`InMemorySpanExporter` for instrumentation unit tests.** Do not mock the OTel API. Use `InMemorySpanExporter` to capture spans in memory and assert on their attributes.

### The Five Anti-patterns to Eliminate

1. **Inline attribute name strings.** `span.set_attribute("gen_ai.system", "openai")` scattered across files. When the attribute renames, you grep and hope. Use a semconv module.

2. **High-cardinality metric attributes.** `attributes={"user_id": user_id, "request_id": request_id}` on metric recordings. This creates one time-series per user, exhausting backend storage.

3. **`SimpleSpanProcessor` in production.** Blocks the request hot path on span export. At any non-trivial scale, this creates latency coupling between your application and your observability backend.

4. **Missing `shutdown()`.** Deploying without lifespan cleanup. The last N seconds of telemetry — including spans from the requests that triggered the deployment-ending event — are silently lost.

5. **Mocking the OTel API in tests.** `patch("opentelemetry.trace.get_tracer")` tests that your code calls `get_tracer()`, not that it produces correct spans. Use `InMemorySpanExporter` to test actual span content.

---

*Last updated: 2026-03-18. Semconv baseline: opentelemetry-semantic-conventions 0.60b1 (GenAI conventions at Development stability).*
