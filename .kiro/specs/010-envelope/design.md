# Design: 010a Envelope

## Architectural reminder

The durable product is not the inline gateway runtime.

The durable product is the observable contract and the decision/evidence core that can
survive across runtimes.

This spec exists to make that contract real in code.

---

## Files

### `core/envelope.py`

This module defines the typed envelope primitives.

Recommended shape:

- enums for:
  - `EnvelopeStatus`
  - `CostSource`
  - `CircuitState`
- a dataclass for the envelope itself
- helper methods for serialization if needed (`to_dict()` is enough)

The envelope should be explicit and boring. This is not a dynamic metadata bag.

A reasonable v0 structure is:

- `schema_version: str`
- identity/context:
  - `request_id`
  - `trace_id | None`
  - `span_id | None`
  - `tenant_id`
  - `caller_id | None`
  - `use_case | None`
  - `route`
- model selection:
  - `provider_requested | None`
  - `model_requested | None`
  - `provider_selected | None`
  - `model_selected | None`
  - `model_tier | None`
  - `routing_decision | None`
  - `routing_reason | None`
- economics:
  - `tokens_in | None`
  - `tokens_out | None`
  - `tokens_total | None`
  - `estimated_cost_usd | None`
  - `cost_source`
- reliability:
  - `latency_ms | None`
  - `status`
  - `error_type | None`
  - `retry_count | None`
  - `fallback_triggered | None`
  - `fallback_reason | None`
  - `circuit_state | None`
- governance:
  - `policy_input_class | None`
  - `policy_decision | None`
  - `policy_mode | None`
  - `redaction_applied | None`
  - `pii_detected | None`
- cache/eval:
  - `cache_eligible | None`
  - `cache_strategy | None`
  - `cache_hit | None`
  - `cache_key_fingerprint | None`
  - `cache_key_algorithm | None`
  - `cache_lookup_confidence | None`
  - `eval_hooks | tuple[str, ...]`
  - `audit_tags | dict[str, str]`

### `core/semconv.py`

This module defines only the project-specific constants needed in 010a.

Important bridging rule:

In spec 010a, define the `llmscope.*` namespace constants needed for the new additive fields,
but do **not** perform a broad migration of all existing custom runtime attributes.

For this spec, only new additive runtime usage should be required for:

- `llmscope.schema_version`
- `llmscope.cost_source`

Other project namespace constants may be declared here for future use, but should not trigger
codebase-wide migration yet.

Use official `gen_ai.*` semantics wherever official semconv already covers the concept.

### `tests/test_envelope.py`

This test file should prove:

1. `core.envelope` imports without importing `fastapi` or `starlette`
2. enums behave as expected
3. the envelope can be instantiated with minimal required fields
4. serialization / dict conversion works
5. `cost_source`, `circuit_state`, and `cache_key_algorithm` are present

---

## Lifecycle modeling

The code in this spec does not need to fully emit all lifecycle stages from the gateway.

It does need to model them clearly enough that later runtime extraction has an explicit target.

Important clarification:

The dataclass supports all lifecycle states, but current runtime wiring in this phase is
expected to be primarily terminal-event oriented.

Do not let Kiro interpret this spec as a mandate to build a lifecycle engine.

---

## OTel mapping

Use official semconv where possible.

Examples:

- `gen_ai.request.model`
- `gen_ai.response.model`
- token counts where official conventions already exist

Use `llmscope.*` only for project-specific facts, such as:

- `llmscope.schema_version`
- `llmscope.cost_source`
- `llmscope.policy_decision`
- `llmscope.routing_reason`
- `llmscope.status` when needed for degraded states

Important status rule:

- gateway/client success should map to OTel `UNSET`, not `OK`

---

## JSONL / runtime outputs

This spec may add fields to emitted runtime evidence only additively.

Meaning:

- existing field names and existing field types must not be broken
- adding `schema_version` is allowed
- adding `cost_source` is allowed

This spec is not a license to redesign the runtime evidence shape.

---

## Seam discipline

The contract must be usable without:

- `Request`
- `Response`
- `BaseHTTPMiddleware`
- ASGI lifecycle objects

If the envelope code cannot be imported into a plain Python script without framework baggage,
the spec is not satisfied.
