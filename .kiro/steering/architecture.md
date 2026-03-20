# Architecture Steering

## Core principle

The product is the envelope. The gateway, SDK, sidecar, and exporters are runtimes around it.

Every architectural decision must answer: does this produce or consume the envelope correctly?
If it invents parallel semantics in runtime-native code without contributing to the envelope
contract, it belongs outside the core.

---

## Three planes

### Instrumentation plane

Runtime-specific code that intercepts LLM calls and emits envelope events.

Current implementations:
- `app/middleware/` — auth, rate limiting (gateway-specific)
- `gateway/client.py` — call interception, span creation, lifecycle event emission
- `app/routes/` — request reception, routing call, response return

Future implementations:
- `sdk/` — library clients import directly (no separate process)
- `sidecar/` — out-of-band process receiving event copies

### Decision plane

Runtime-agnostic code that computes typed, serializable decisions.

Lives in: `core/`

Responsibilities:
- Policy evaluation → `PolicyDecision`
- Routing recommendation → `RoutingDecision`
- Budget control → `BudgetDecision`
- Cache eligibility → `cache_eligible: bool`, `cache_key_fingerprint: str`
- Cost estimation → `estimated_cost_usd: float`, `cost_source: CostSource`
- Circuit state → `CircuitState`

Rule: **no decision plane module may import FastAPI, Starlette, or ASGI types.** Decision
logic must be expressible as a pure function over typed inputs.

### Evidence plane

Runtime-agnostic projections of the envelope to external sinks.

Lives in: `gateway/telemetry.py` (current), future adapters in `core/exporters/`

Responsibilities:
- OTel metrics emission
- JSONL audit artifact
- Langfuse generation export
- Cost ledger snapshots

Rule: **exporters project from the envelope contract, not from ad hoc runtime objects.**

---

## Full system architecture (current gateway runtime)

```
HTTP Request
  │
  ▼
[APIKeyMiddleware]         app/middleware/auth.py
  │  → 401 on missing/invalid X-API-Key header
  ▼
[RateLimitMiddleware]      app/middleware/rate_limit.py
  │  → 429 on per-key sliding window overflow
  ▼
[FastAPIInstrumentor]      OTel SERVER span — request.received
  │
  ▼
Route Handler (async)      app/routes/
  │
  ├── /classify-complexity → determine_complexity()   (no LLM call)
  │
  ├── /answer-routed       → determine_complexity()
  │                           → await call_llm()
  │
  └── /conversation-turn   → prepare_context()
                             → await call_llm()
                                   │
                                   ▼
                             [gateway/client.py]       OTel CLIENT span
                                   │
                                   ├── Envelope: request.received
                                   ├── RoutePolicy lookup
                                   ├── Envelope: request.routed (span event)
                                   ├── SemanticCache.get()    → return cached if HIT
                                   ├── CircuitBreaker.check() → 503 if OPEN
                                   ├── await _call_provider() → AsyncOpenAI
                                   ├── estimate_cost() → CostSource.estimated_local_snapshot
                                   ├── SemanticCache.put()
                                   ├── Envelope: request.completed or request.failed
                                   └── emit()                 → OTel metrics + JSONL
```

---

## Layer responsibilities

### `core/` (decision + evidence, runtime-agnostic)

- `core/envelope.py` — `LLMRequestEnvelope` dataclass and all enums
- `llmscope/semconv.py` — `llmscope.*` attribute name constants

Future additions (spec 011+):
- `core/policy.py` — typed policy evaluation
- `core/cost_model.py` — promoted from `gateway/cost_model.py`
- `core/exporters/` — JSONL, OTLP, Langfuse projections

### `gateway/` (gateway runtime — instrumentation + actuation)

- `client.py` — `async def call_llm()`: sole provider call path; emits lifecycle events
- `cache.py` — exact cache module (opinado); semantic cache interface (adapter)
- `circuit_breaker.py` — three-state machine for provider resilience
- `cost_model.py` — pricing snapshot (to be promoted to `core/` in spec 011)
- `policies.py` — `RoutePolicy` frozen dataclass; model-for-tier mapping
- `telemetry.py` — `emit()`: dual-write OTel + JSONL (to be refactored in spec 011)
- `otel_setup.py` — `setup_otel()` / `shutdown_otel()`: global provider config
- `semconv.py` — `gen_ai.*` constants; `resolve_attrs()` for migration window

### `app/` (FastAPI application layer)

- `middleware/` — auth and rate limiting; gateway-specific
- `routes/` — thin handlers; call services or gateway; no business logic
- `routes/health.py` — `/healthz` and `/readyz`; never fail due to OTel state
- `services/` — domain logic without gateway coupling (routing, context, token counting)
- `schemas/` — Pydantic request/response contracts; frozen

---

## Hard architectural rules

These rules may not be overridden by any spec task:

1. **No direct OpenAI calls outside `gateway/client.py`.**
   Exception: `openai.embeddings.create()` in `gateway/cache.py` (semantic layer) and
   `app/services/routing.py` (anchor embeddings) — these bypass `call_llm` to avoid
   recursive cost tracking.

2. **No route handler contains auth, rate-limit, caching, retry, or circuit-breaker logic.**

3. **The cache lives in `gateway/cache.py` only.** Routes must not import from it.

4. **The circuit breaker lives in `gateway/circuit_breaker.py` only.** Routes must not
   import from it.

5. **Authentication is enforced at middleware layer only.** No `Depends()` auth guards in
   route handlers.

6. **tiktoken is imported only in `app/services/token_counter.py`.**

7. **`gateway/client.py` is the sole source of telemetry emission** in the gateway runtime.
   Route handlers must not call `emit()` directly.

8. **The JSONL telemetry format is frozen.** Existing field names and types must not change.
   New optional fields may be added.

9. **The three route paths are frozen:** `/classify-complexity`, `/answer-routed`,
   `/conversation-turn`.

10. **Decision plane code must not import FastAPI or ASGI types.** Functions in `core/` must
    be callable from a plain Python script with no web framework present.

---

## Seam discipline (critical for specs 007–011)

Every new function added to `gateway/` or `core/` must satisfy this test before merging:

> Could this logic be called from a Python script that has no FastAPI installed?

If the answer is yes, the seam is clean. If no, the logic has imported framework assumptions
that will block SDK and sidecar runtimes.

Practical rule: decision logic must receive typed plain-Python inputs, not `Request` objects,
`Response` objects, or ASGI lifecycle constructs. The middleware or route handler is the thin
adapter that extracts values and calls the clean function.

---

## Dependency rules between layers

```
routes     → services, gateway/client (await call_llm)
services   → token_counter, openai.embeddings (routing only)
gateway    → core, openai (AsyncOpenAI), telemetry, cost_model, policies, cache, circuit_breaker
middleware → fastapi internals only
core       → stdlib only; no fastapi, no openai, no starlette
```

Reverse dependencies (e.g., gateway importing from routes, core importing from gateway) are
forbidden.

---

## Configuration architecture

All configuration is environment-variable driven. No config files.

Required env vars (app refuses to start without these):
- `OPENAI_API_KEY`
- `APP_API_KEY`

Optional env vars (all have safe defaults):
- `RATE_LIMIT_RPM=60`
- `MAX_CONTEXT_TOKENS=8192`
- `SEMANTIC_CACHE_ENABLED=false`
- `SEMANTIC_CACHE_THRESHOLD=0.97`
- `CIRCUIT_BREAKER_FAILURE_THRESHOLD=5`
- `CIRCUIT_BREAKER_RESET_TIMEOUT_S=30`
- `CONVERSATION_TTL_SECONDS=3600`
- `REDIS_URL`
- `ROUTING_USE_EMBEDDINGS=true`
- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_SDK_DISABLED=false`

---

## What must NOT be added

Permanently out of scope for this repository:

- Streaming responses
- Tool/function calling
- Background task queues
- Plugin/registry systems
- Agent orchestration
- Fine-tuning pipeline
- Dashboard or UI
- OAuth / multi-user auth
- Database migrations framework
- Distributed rate limiting (comment documenting the upgrade path is sufficient)

Multi-provider abstraction is NOT in this list. It is a future core track objective,
implemented as adapters in `core/` — not as a gateway-level abstraction.

---

## Simplicity rule

When two designs solve the same problem, prefer the one with:
- fewer files
- fewer moving parts
- easier local inspection
- no speculative extensibility

A design that is "flexible for future needs" but harder to read today is the wrong design.
The exception: seams required for runtime portability are not speculative — they are required
by the architecture.
