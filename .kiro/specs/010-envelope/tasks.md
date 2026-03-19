# Tasks: 010a Envelope

## Prerequisite gate

Run before starting any task:

```bash
OTEL_SDK_DISABLED=true python3 -m pytest tests/ -v
```

If the suite is not green, stop and fix that first.

---

## Layer 1 — Create the contract in isolation

### Task 1 — Create `core/` package

- [ ] 1.1 Create `core/__init__.py`
- [ ] 1.2 Verify:
  ```bash
  python3 -c "import core; print('ok')"
  ```

**Acceptance**: `core` imports cleanly.

---

### Task 2 — Create `core/semconv.py`

- [ ] 2.1 Create `core/semconv.py`
- [ ] 2.2 Define project namespace constants for 010a
- [ ] 2.3 Include at minimum:
  - `ATTR_LLMSCOPE_SCHEMA_VERSION = "llmscope.schema_version"`
  - `ATTR_LLMSCOPE_COST_SOURCE = "llmscope.cost_source"`
- [ ] 2.4 Optionally define future constants, but do not trigger broad migration in this spec
- [ ] 2.5 `ruff check core/semconv.py`
- [ ] 2.6 `mypy core/semconv.py --ignore-missing-imports`

**Acceptance**: module imports, constants exist, no framework imports.

---

### Task 3 — Create `core/envelope.py`

- [ ] 3.1 Create `core/envelope.py`
- [ ] 3.2 Add enums:
  - `EnvelopeStatus`
  - `CostSource`
  - `CircuitState`
- [ ] 3.3 Create the envelope dataclass with explicit fields across the six semantic blocks
- [ ] 3.4 Include:
  - `schema_version`
  - `cost_source`
  - `circuit_state`
  - `cache_key_algorithm`
- [ ] 3.5 Add a simple `to_dict()` helper if useful
- [ ] 3.6 Do **not** import `fastapi`, `starlette`, or ASGI objects
- [ ] 3.7 `ruff check core/envelope.py`
- [ ] 3.8 `mypy core/envelope.py --ignore-missing-imports`

**Acceptance**: the envelope is typed, importable, and framework-free.

---

### Task 4 — Create `tests/test_envelope.py`

- [ ] 4.1 Create `tests/test_envelope.py`
- [ ] 4.2 Add tests:
  - import of `core.envelope` succeeds
  - `fastapi` and `starlette` are not imported as a side effect
  - `CostSource` enum values exist
  - `CircuitState` enum values exist
  - minimal envelope instance can be created
  - `cache_key_algorithm` is present
  - `schema_version` is present
- [ ] 4.3 Run:
  ```bash
  OTEL_SDK_DISABLED=true python3 -m pytest tests/test_envelope.py -v
  ```

**Acceptance**: all envelope tests pass.

---

## Checkpoint

At this point, stop and inspect manually before touching runtime wiring.

The purpose of this checkpoint is to confirm that the contract exists in isolation.

Do not skip this checkpoint.

---

## Layer 2 — Additive runtime wiring only

### Task 5 — Add additive runtime use of `schema_version`

- [ ] 5.1 Identify current runtime evidence emission path(s)
- [ ] 5.2 Add `schema_version` additively where appropriate
- [ ] 5.3 Do not break existing field names or field types

**Acceptance**: runtime outputs remain backward-compatible.

---

### Task 6 — Add additive runtime use of `cost_source`

- [ ] 6.1 Identify where estimated cost is emitted
- [ ] 6.2 Add `cost_source` additively
- [ ] 6.3 For existing estimated cost paths, use `estimated_local_snapshot`
- [ ] 6.4 Do not perform broad migration of legacy custom semconv attributes

**Acceptance**: cost provenance is explicit without schema breakage.

---

## Full verification

### Task 7 — Verification

- [ ] 7.1 Run:
  ```bash
  OTEL_SDK_DISABLED=true python3 -m pytest tests/test_envelope.py -v
  OTEL_SDK_DISABLED=true python3 -m pytest tests/ -v
  ```
- [ ] 7.2 Run:
  ```bash
  python3 -m ruff check core/
  python3 -m mypy core/ --ignore-missing-imports
  ```
- [ ] 7.3 Manually verify:
  - no framework imports in `core/`
  - `schema_version` is additive
  - `cost_source` is additive
  - no broad namespace migration happened

**Acceptance**: contract exists, tests pass, runtime outputs remain compatible.

---

## Completion criteria

This spec is complete when:

- `core/__init__.py` exists
- `core/semconv.py` exists
- `core/envelope.py` exists
- `tests/test_envelope.py` passes
- `schema_version` and `cost_source` are wired additively
- `cost_source`, `circuit_state`, and `cache_key_algorithm` are modeled explicitly
- `core/` imports without framework dependencies
