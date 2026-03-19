# Product Steering

## What this project is

LLMScope is a portable, versioned, observable contract for cost, policy, routing, cache,
reliability, and evidence around LLM requests — and the reference implementations that make
that contract operational.

The product is the envelope. Gateway, SDK, sidecar, and exporters are runtimes and projections
around that contract. If the envelope is stable and well-specified, the rest of the system is
implementation detail that can evolve, swap, or multiply without breaking the core value.

The project proves three operational ideas that apply across industries:

1. A significant fraction of LLM requests can be served by a cheaper model or from cache with
   no quality loss — but only if routing and cache decisions are observable, attributable, and
   governed by explicit policy.
2. Cost is not visible at the infrastructure layer. It must be attributed at the application
   layer where token counts, model selection, and cache hits are known.
3. Governance and audit evidence must be first-class outputs, not afterthoughts — especially in
   regulated environments (fintech, health, legal) where decision lineage is a compliance
   requirement.

## What this project is not

- A consumer-facing application
- A multi-tenant SaaS product
- A general-purpose agent platform or orchestration framework
- A streaming or function-calling demo
- A dashboard or UI project
- A managed service for prompt engineering
- A replacement for your application's business logic

## Architecture: three planes, multiple runtimes

The system is organized into three planes:

**Instrumentation plane** — SDK wrappers, middleware, gateway interceptors that extract request
context and emit envelope events. Runtime-specific. Today: FastAPI middleware, gateway client.
Future: SDK library, sidecar collector.

**Decision plane** — policy evaluation, routing recommendations, budget control, cache
eligibility, circuit state, PII annotation, eval hooks. Runtime-agnostic. Lives in `core/`.
Produces `PolicyDecision`, `RoutingDecision`, `BudgetDecision`, and `CostSource` — all typed,
all serializable, all auditable without requiring FastAPI or ASGI.

**Evidence plane** — OTLP export, Langfuse adapter, JSONL audit artifacts, cost ledgers, eval
datasets, replay artifacts. Runtime-agnostic projections of the envelope contract.

### Current runtime distribution: gateway

The FastAPI gateway is the first runtime distribution. It applies instrumentation, decision,
and evidence in a single process. It is the reference implementation for demos and for clients
who want turnkey adoption with minimal integration effort.

The gateway is NOT the product. It is one way to deploy the product. This distinction matters
for architecture decisions: the gateway can evolve as an application without contaminating the
core contract.

### Future runtime distributions

- **SDK** — a Python library clients import. Produces the same envelope without a separate
  process. Appropriate for services that cannot route through a proxy.
- **Sidecar/collector** — a lightweight process that receives copies of events, computes
  decisions out-of-band, and makes signals available for the application to consume. Zero blast
  radius on the request path.

## Adoption gradient

The project is designed for incremental adoption:

1. **Observe** — instrument calls, emit envelope events, export to Langfuse or OTLP. No policy
   enforcement. No routing changes. Blast radius: zero.
2. **Attribute** — enable cost attribution, cache savings measurement, routing distribution
   reporting. Read-only signals.
3. **Control** — activate policy enforcement, budget gates, circuit breaker, routing rules.
   Enforcement layer added on top of observation.

New capabilities should default to observe-only mode before enforcement. This applies to cache
(emit `would_hit` before serving cached responses), policy (emit decision before blocking), and
routing rules (run in shadow before redirecting traffic).

## Spec tracks

### Core track (primary)

These specs define the contract and the runtime-agnostic core:

| Spec | Description |
|---|---|
| 001–006 | Baseline gateway: reference app, gateway choke point, eval harness, reporting, real models, health/CI |
| 007–009 | Gateway runtime hardening: auth, async retry, token counting |
| 010a | **Envelope spec** — the contract everything else is built around |
| 010 | Cache: exact cache as core capability, semantic cache as interface/adapter |
| 011 | Core extraction: promote `core/` as runtime-agnostic decision+evidence engine |

### Gateway runtime track (optional, demo-enhancing)

These specs add value to the gateway distribution but are not part of the core contract:

| Spec | Description |
|---|---|
| 012 | Circuit breaker — gateway reliability feature |
| 013 | Server-side conversation persistence — gateway application feature |
| 014 | Embedding-based routing classifier — gateway routing enhancement |

Specs in the gateway runtime track should not be treated as prerequisites for the core track.
They can be implemented in parallel or skipped without affecting the portability of the core.

## Product success criteria

The core is complete when:

1. `core/envelope.py` exists with the full `LLMRequestEnvelope` contract and all enums.
2. A script outside FastAPI can instantiate the decision engine, process an envelope, and export
   to JSONL without importing `fastapi` or `starlette`.
3. `cost_source` is present and enforced on every economics emission path.
4. The gateway emits all five lifecycle stages (`request.received` through `request.evaluated`)
   with correct OTel mapping.
5. Exact cache fingerprints canonical envelope input, not raw framework objects.
6. All 200+ tests pass. `ruff check`, `ruff format --check`, and `mypy` pass on all packages.

The gateway distribution is complete when additionally:

7. A live call to `/answer-routed` returns a real OpenAI response.
8. Unauthenticated requests return HTTP 401. Over-limit requests return HTTP 429.
9. Health probes work: `/healthz` always 200, `/readyz` 200/503 based on startup state.
10. A second identical prompt hits the cache with `cost_source = cached_zero`.
11. A third party can clone, install, and run the full workload without manual intervention.

## What requires explicit approval to add

- New routes beyond the three existing ones
- New LLM providers in the gateway (providers in core adapter interface are fine)
- Streaming response support
- Tool/function calling
- OAuth or multi-user auth
- Dashboard or UI
- Background task queues
- Any feature that requires a new required field in the v0.1.0 envelope (additive optional fields are fine)

## Final constraint

If a proposed change does not improve measurability, attributability, policy expressiveness,
cache observability, routing clarity, context control, evidence quality, or portability across
runtimes — it belongs outside this project's scope.
