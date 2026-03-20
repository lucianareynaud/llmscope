# OpenTelemetry Steering

## OTel is already implemented — do not reinitialise it

The OpenTelemetry SDK is fully configured. The following are complete and frozen:
- `gateway/otel_setup.py` — `setup_otel()` / `shutdown_otel()`
- `gateway/telemetry.py` — `emit()` with OTel metrics + JSONL
- `app/main.py` — lifespan handler calling `setup_otel()` then `FastAPIInstrumentor`
- All OTel packages pinned in `requirements.txt`

**Do not add a second `setup_otel()` call. Do not create a second TracerProvider or
MeterProvider. Do not import `set_tracer_provider` outside `gateway/otel_setup.py`.**

---

## SDK disable in tests

All test runs must set `OTEL_SDK_DISABLED=true`:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v
```

---

## Envelope lifecycle model and OTel mapping

The envelope defines five lifecycle stages. Each stage maps explicitly to OTel so that all
runtimes produce consistent traces. See `010a-envelope/spec.md` for the full field list per
stage.

### `request.received` → span start attributes

At span creation, set these as span attributes:

```python
span.set_attribute("llmscope.schema_version", "0.1.0")
span.set_attribute("llmscope.request_id", request_id)
span.set_attribute("llmscope.tenant_id", tenant_id)
span.set_attribute("llmscope.use_case", use_case)
span.set_attribute("llmscope.route", route)
span.set_attribute("llmscope.runtime_mode", "gateway")
# gen_ai.* from existing semconv.py:
span.set_attribute(ATTR_GEN_AI_REQUEST_MODEL, model_requested)
span.set_attribute(ATTR_GEN_AI_SYSTEM, VAL_GEN_AI_SYSTEM_OPENAI)
```

These represent input context — not terminal outcome.

### `request.routed` → span event

Emit an OTel span event named `"request.routed"` so routing timing is visible in trace
chronology:

```python
span.add_event("request.routed", attributes={
    ATTR_GEN_AI_RESPONSE_MODEL: model_selected,
    "llmscope.model_tier": model_tier,
    "llmscope.routing_decision": routing_decision,
    "llmscope.routing_reason": routing_reason,
    "llmscope.policy_decision": policy_decision,
    "llmscope.policy_mode": policy_mode,
})
```

Also update span attributes for queryability (backends can query span attributes but not
always span event attributes efficiently).

### `request.completed` → terminal span attributes (success)

```python
span.set_attribute(ATTR_GEN_AI_USAGE_INPUT_TOKENS, tokens_in)
span.set_attribute(ATTR_GEN_AI_USAGE_OUTPUT_TOKENS, tokens_out)
span.set_attribute("llmscope.tokens_total", tokens_in + tokens_out)
span.set_attribute("llmscope.estimated_cost_usd", estimated_cost_usd)
span.set_attribute("llmscope.cost_source", cost_source)
span.set_attribute("llmscope.status", "ok")  # or "cached"
span.set_attribute("llmscope.latency_ms", latency_ms)
span.set_attribute("llmscope.cache_hit", cache_hit)
span.set_attribute("llmscope.cache_key_fingerprint", cache_key_fingerprint)
# Do NOT call span.set_status(StatusCode.OK) for library/gateway CLIENT spans.
# Leave span status as UNSET — OTel convention for "no error detected".
# Backends correctly treat UNSET as success in error-rate calculations.
# StatusCode.OK is reserved for explicit application-level success assertions.
```

### `request.failed` → terminal span attributes (error)

```python
span.record_exception(exc)                              # stack trace as span event
span.set_status(Status(StatusCode.ERROR, str(exc)))     # marks span as failed
span.set_attribute("error.type", type(exc).__name__)    # OTel standard
span.set_attribute("llmscope.status", "error")           # semantic truth
span.set_attribute("llmscope.error_type", error_type)    # internal taxonomy
span.set_attribute("llmscope.retry_count", retry_count)
span.set_attribute("llmscope.latency_ms", latency_ms)
span.set_attribute("llmscope.cost_source", "degraded_unknown")  # when cost not known
```

OTel status mapping:
- envelope `status = ok` or `cached` → OTel span status `UNSET`
- envelope `status = error` → OTel span status `ERROR`
- envelope `status = degraded` → OTel span status `UNSET`; `llmscope.status=degraded` explicit
- envelope `status = denied` → OTel span status `ERROR` when denial is operationally a failure

**The envelope is the semantic source of truth. OTel status is a compressed binary signal
for backends. Never infer envelope semantics from OTel status alone.**

### `request.evaluated` → async span event

```python
span.add_event("request.evaluated", attributes={
    "llmscope.eval_hooks": str(eval_hooks),
    "llmscope.feedback_signal": feedback_signal,
})
```

If evaluation happens asynchronously in another process, correlate via `trace_id` and
`request_id`. Emit onto the same trace or a linked trace depending on runtime constraints.

---

## How to add OTel spans

Use the global tracer from `opentelemetry.trace`:

```python
from opentelemetry import trace

_tracer = trace.get_tracer("llm_gateway", version="0.1.0")

with _tracer.start_as_current_span("chat gpt-4o-mini", kind=trace.SpanKind.CLIENT) as span:
    # set request.received attributes
    try:
        # ... call provider ...
        # set request.completed attributes
        # Leave span status UNSET on success
    except Exception as exc:
        span.record_exception(exc)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
        raise
```

---

## Namespace policy

- `gen_ai.*` — semconv official fields. Import from `gateway/semconv.py` constants only.
  Never import directly from `opentelemetry.semconv._incubating` in application code.
- `llmscope.*` — proprietary control-plane fields. Import from `llmscope/semconv.py` constants.
  Replaces `llm_gateway.*` progressively. Do not introduce new `llm_gateway.*` attributes.
- Do not invent new attribute namespaces without updating `010a-envelope/spec.md` first.

---

## Span kind rules

| Span context | SpanKind |
|---|---|
| HTTP request processing | `SERVER` — set by `FastAPIInstrumentor` automatically |
| Outgoing LLM provider call | `CLIENT` |
| Internal service call | No span — too noisy, no I/O |

---

## Semantic conventions to follow

All LLM-related spans and metrics must use `gen_ai.*` semconv from `gateway/semconv.py`
plus `llmscope.*` from `llmscope/semconv.py` (after spec 010a).

### Span attributes — full set

| Attribute | Source | Stage |
|---|---|---|
| `gen_ai.system` / `gen_ai.provider.name` | gateway/semconv.py | received |
| `gen_ai.operation.name` | gateway/semconv.py | received |
| `gen_ai.request.model` | gateway/semconv.py | received |
| `gen_ai.request.max_tokens` | gateway/semconv.py | received |
| `gen_ai.response.model` | gateway/semconv.py | routed (event) |
| `gen_ai.usage.input_tokens` | gateway/semconv.py | completed |
| `gen_ai.usage.output_tokens` | gateway/semconv.py | completed |
| `llmscope.schema_version` | llmscope/semconv.py | received |
| `llmscope.request_id` | llmscope/semconv.py | received |
| `llmscope.tenant_id` | llmscope/semconv.py | received |
| `llmscope.use_case` | llmscope/semconv.py | received |
| `llmscope.route` | llmscope/semconv.py | received |
| `llmscope.runtime_mode` | llmscope/semconv.py | received |
| `llmscope.model_tier` | llmscope/semconv.py | routed |
| `llmscope.routing_decision` | llmscope/semconv.py | routed |
| `llmscope.policy_decision` | llmscope/semconv.py | routed |
| `llmscope.policy_mode` | llmscope/semconv.py | routed |
| `llmscope.estimated_cost_usd` | llmscope/semconv.py | completed |
| `llmscope.cost_source` | llmscope/semconv.py | completed |
| `llmscope.status` | llmscope/semconv.py | completed/failed |
| `llmscope.latency_ms` | llmscope/semconv.py | completed/failed |
| `llmscope.cache_strategy` | llmscope/semconv.py | completed |
| `llmscope.cache_hit` | llmscope/semconv.py | completed |
| `llmscope.cache_key_fingerprint` | llmscope/semconv.py | completed |
| `llmscope.circuit_state` | llmscope/semconv.py | completed/failed |
| `llmscope.retry_count` | llmscope/semconv.py | failed |
| `error.type` | OTel standard | failed |

### Metrics (from `gateway/telemetry.py`)

| Metric name | Kind | Unit |
|---|---|---|
| `gen_ai.client.token.usage` | Histogram | `{token}` |
| `gen_ai.client.operation.duration` | Histogram | `s` |
| `llm_gateway.estimated_cost_usd` | Counter | `USD` |
| `llm_gateway.requests` | Counter | `{request}` |

Do not invent new metric names. Check GenAI semconv spec first.

---

## OTLP exporter configuration

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

If not set, falls back to console exporter. The app must not fail to start when no collector
is running.

---

## Adding new OTel metrics

Add to `gateway/telemetry.py` alongside existing metrics:

```python
_my_new_counter = _meter.create_counter(
    "llm_gateway.my_metric",
    unit="{unit}",
    description="What this counts",
)
```

Do not create meters in route handlers, services, or middleware. All metrics live in
`gateway/telemetry.py`.

---

## JSONL telemetry format (frozen fields)

These fields must never change name or type. Existing consumers read them by name.

New optional fields from the envelope may be appended — they are backward compatible.

```json
{
  "timestamp": "ISO-8601 string",
  "request_id": "uuid",
  "route": "/answer-routed",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "tokens_in": 42,
  "tokens_out": 128,
  "latency_ms": 834.5,
  "estimated_cost_usd": 0.000094,
  "cost_source": "estimated_local_snapshot",
  "status": "ok",
  "error_type": null,
  "cache_hit": false,
  "schema_version": "0.1.0"
}
```
