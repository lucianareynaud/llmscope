# Workflow Steering

## Core workflow rule

Work one spec at a time. Never implement, design, or generate tasks for a future spec while
the current spec is in progress.

## Spec lifecycle

Every spec must pass through these stages in order:

```
1. prerequisite gate   → run test suite from prior spec; must be fully green
2. implementation      → execute tasks in the spec's tasks.md, in order
3. tests               → write or update tests before marking tasks complete
4. verification        → run full test suite + ruff + mypy; must all pass
5. commit              → commit with a descriptive message
```

## The prerequisite gate (critical)

Before starting any task in a spec, run:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v
```

If any test fails, fix the failure before touching any file in the current spec.

---

## Spec sequence

### Core track (execute in order)

| Spec | Name | Gate (passing tests before start) |
|---|---|---|
| 001–006 | Baseline (complete) | — |
| 007 | Authentication and rate limiting | ≥ 192 |
| 008 | Async-safe gateway retry | ≥ 201 |
| 009 | Accurate token counting | ≥ 209 |
| **010a** | **Envelope contract** | ≥ 217 |
| **010** | **Cache: exact as core, semantic as interface** | ≥ 225 |
| **011** | **Core extraction: runtime-agnostic engine** | ≥ 233 |

**Specs 010a, 010, and 011 are the highest-leverage work in the repository.** They transform
the project from a well-instrumented gateway into a portable control-plane substrate. Do not
skip or reorder them.

### Gateway runtime track (independent, optional)

These specs enhance the gateway distribution and do not block the core track. They may be
implemented after spec 011 or in parallel if resources allow.

| Spec | Name | Description |
|---|---|---|
| 012 | Circuit breaker | Gateway provider resilience feature |
| 013 | Conversation persistence | Gateway server-side history feature |
| 014 | Embedding-based routing | Gateway routing quality enhancement |

Gateway runtime track specs do not have test count gates. Run the full suite before each and
ensure zero regressions.

---

## Seam discipline for specs 007–009

Specs 007-009 add features to the gateway runtime before the core is extracted. To avoid
introducing irreversible coupling, every new function added during these specs must pass this
test before merging:

**Could this logic be called from a plain Python script with no FastAPI installed?**

Concretely:
- Decision logic receives typed plain-Python arguments, not `Request` or `Response` objects.
- Any new field emitted in JSONL or as a span attribute must correspond to a named field in
  `010a-envelope/spec.md`. No ad hoc fields.
- Middleware is the thin adapter that extracts values and calls clean functions — not the place
  where decisions live.

---

## Files to read at the start of any spec

Always read these before writing any code:

- `.kiro/specs/NNN-name/spec.md` — scope, acceptance criteria, what is frozen
- `.kiro/specs/NNN-name/design.md` — technical blueprint
- `.kiro/specs/NNN-name/tasks.md` — ordered task list with acceptance checks
- `.kiro/specs/010a-envelope/spec.md` — envelope contract (always; governs all field semantics)

---

## What is frozen throughout all specs

These must not change regardless of what a task asks:

- The three route paths and their Pydantic schemas
- `gateway/telemetry.py` JSONL **existing** field names and types — renaming, removing, or
  changing the type of existing fields is forbidden. Adding new optional envelope fields to
  the JSONL record is **explicitly allowed** and does not violate the freeze. New fields that
  `reporting/make_report.py` does not reference are silently ignored by existing consumers.
- `reporting/make_report.py` and the artifact format it reads
- The `evals/` harness structure, datasets, and runner interfaces
- `gateway/otel_setup.py` setup/shutdown pattern
- The `RoutePolicy` frozen dataclass structure
- `010a-envelope/spec.md` core field names and enum values for **existing required fields**.
  Adding new optional fields follows the versioning rules in that spec (additive patch/minor
  changes are explicitly allowed without a version bump). Breaking changes require a version
  bump per the envelope versioning policy.

---

## Commit message rules

Commit messages must identify:

1. Which spec and track the commit belongs to
2. Which files were changed
3. What acceptance criteria were satisfied

Example:

```
spec-007 [gateway-runtime]: add APIKeyMiddleware and RateLimitMiddleware

- app/middleware/auth.py: X-API-Key validation, /healthz /readyz exempt
- app/middleware/rate_limit.py: per-key deque sliding window
- app/main.py: register both middlewares, APP_API_KEY startup validation
- tests/test_auth.py: 5 tests
- tests/test_rate_limit.py: 4 tests

Seam check: auth validation logic is a pure function over (provided_key, expected_key);
middleware is the thin adapter. No FastAPI types in decision path.

201 tests passing. ruff, mypy clean.
```

---

## CI requirements

Every commit that modifies source files must pass locally before pushing:

```bash
ruff check .
ruff format --check .
mypy app/ gateway/ core/ evals/ reporting/ --ignore-missing-imports
OTEL_SDK_DISABLED=true pytest tests/ -v
```

---

## When a task introduces a regression

If a task causes a previously passing test to fail:

1. Stop immediately.
2. Identify whether the failure is in a test that tests the new code or in a pre-existing test.
3. If a pre-existing test breaks, revert the relevant change and redesign so the existing test
   continues to pass.
4. Tests are the truth.

---

## Definition of done for a spec

A spec is complete only when:

1. All sub-task checkboxes in `tasks.md` are satisfied
2. The task's `**Acceptance**` check passes
3. `OTEL_SDK_DISABLED=true pytest tests/ -v` passes (all tests)
4. `ruff check .` exits zero
5. `ruff format --check .` exits zero
6. `mypy app/ gateway/ core/ evals/ reporting/ --ignore-missing-imports` exits zero
7. Seam discipline rule is satisfied for every new function added

## Final rule

Move only when the current spec is demonstrably complete.
Do not trade completion for motion.
Do not trade scope discipline for perceived speed.
