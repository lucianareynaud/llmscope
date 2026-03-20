# Spec: 010a Envelope

## Goal

Define and implement the first runtime-independent envelope contract for LLMScope.

This spec does **not** extract the full core yet. It establishes the product boundary in code:
a versioned, explicit, observable envelope schema that can be produced by the current
gateway runtime and later reused by other runtimes.

The envelope is the durable contract. The gateway is only the first distribution.

## Prerequisite gate

Spec 009 must be complete before starting:

```bash
OTEL_SDK_DISABLED=true python3 -m pytest tests/ -v  # ≥ 211 passed, 1 skipped
```

All checks must pass before any task in this spec begins.

## What this spec adds

- `core/__init__.py`
- `core/envelope.py`
- `llmscope/semconv.py`
- `tests/test_envelope.py`

## What this spec changes

- selected runtime code may add **additive** use of:
  - `schema_version`
  - `cost_source`

## What this spec does NOT change

- no provider abstraction extraction yet
- no semantic cache implementation
- no circuit breaker work
- no conversation persistence work
- no routing/classifier redesign
- no JSONL schema breakage
- no broad namespace migration across the codebase

## Problem

The project has already converged conceptually on a contract-centric architecture,
but the codebase still lacks an explicit runtime-independent envelope definition.

Without that contract:

- semantics remain implicit in gateway code
- evidence surfaces risk drifting
- future runtime extraction becomes file movement instead of product extraction
- cost attribution remains under-specified
- cache and policy semantics have no shared typed substrate

## Acceptance criteria

1. `core/envelope.py` exists and imports cleanly without importing `fastapi` or `starlette`.
2. `llmscope/semconv.py` exists and defines the initial project namespace constants required by this spec.
3. The envelope includes explicit fields for:
   - `schema_version`
   - identity/context
   - model selection
   - economics
   - reliability
   - governance
   - cache/eval
4. `cost_source` is modeled as an explicit enum, not a free-form string.
5. `circuit_state` exists in the reliability block.
6. `cache_key_algorithm` exists for fingerprint/version clarity.
7. OTel mapping uses official `gen_ai.*` semantics where appropriate and project namespace only for project-specific fields.
8. Success does **not** map to OTel span status `OK` by default for gateway/client success paths.
9. `tests/test_envelope.py` verifies that the core envelope can be imported and used without framework imports.
10. This spec may add envelope fields to runtime outputs only **additively**. Existing JSONL field names and types remain backward-compatible.

## Scope boundary

This spec defines the contract. It does not yet complete the extraction of the full core.

That later extraction belongs to spec 011.

## Hard rules

- The envelope is the product boundary.
- The runtime is a distribution.
- `core/` must not import `fastapi`, `starlette`, or ASGI lifecycle objects.
- Use official `gen_ai.*` semantic conventions where they already exist.
- Use `llmscope.*` only for project-specific fields not covered by official semconv.
- Do not create parallel custom fields when official semconv already covers the concept.
- `cost_source` must be explicit and typed.
- `tenant_id="default"` is allowed as a runtime fallback, but must not be treated as a real business identity.
- This spec may introduce `llmscope.schema_version` and `llmscope.cost_source` in runtime emission.
- This spec must not trigger broad migration of legacy custom attributes. That belongs to later extraction work.

## Lifecycle model

The canonical lifecycle stages are:

- `request.received`
- `request.routed`
- `request.completed`
- `request.failed`
- `request.evaluated`

This spec defines the shape and mapping, but does not require the gateway runtime to emit
all five stages immediately.

## OTel mapping rules

- Use span attributes for stable request-level facts known at or before completion.
- Use span events for lifecycle transitions and late-bound facts where appropriate.
- Envelope status:
  - `ok` -> OTel status `UNSET`
  - `cached` -> OTel status `UNSET`
  - `degraded` -> OTel status `UNSET`, plus explicit `llmscope.status="degraded"`
  - `error` -> OTel status `ERROR`
  - `denied` -> OTel status `ERROR` when treated as operational failure in the current runtime

## Required semantic areas

The envelope must define fields across these six blocks:

1. identity / context
2. model selection
3. economics
4. reliability
5. governance
6. cache + evaluation

## Required economics enum

`cost_source` must support at least:

- `estimated_local_snapshot`
- `cached_zero`
- `provider_invoice`
- `degraded_unknown`

## Required exclusions

These must **not** become mandatory first-class fields in the primary envelope:

- raw prompt text
- raw completion text
- embeddings payloads
- full retrieved documents
- full HTTP payloads

They may exist in sink-specific evidence, but not in the primary contract.

## Completion criteria

This spec is complete when:

- `core/envelope.py` exists
- `llmscope/semconv.py` exists
- the envelope schema is importable and typed
- `cost_source`, `circuit_state`, and `cache_key_algorithm` are present
- `tests/test_envelope.py` passes
- runtime use of `schema_version` and `cost_source` is additive only
- no framework imports exist in `core/`
