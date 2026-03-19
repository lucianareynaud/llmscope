# Coding Standards

## Language and runtime
- Python 3.11 exclusively. No Python 3.10 or earlier syntax.
- FastAPI for the HTTP layer. Do not replace or augment with another framework.
- Pydantic v2 for request/response contracts and data validation.
- `AsyncOpenAI` for all provider calls. Never use the synchronous `OpenAI` client.

## Seam discipline (critical — read before adding any new function)

Every new function added to `gateway/` or `core/` must satisfy this rule before merging:

> The function must be callable from a plain Python script with no FastAPI installed.

This means:
- Functions receive typed plain-Python arguments. Never `Request`, `Response`, or ASGI objects.
- Functions return typed plain-Python values. Never framework-native response types.
- Middleware and route handlers are the thin adapters that extract values and call clean functions.
  Decision logic does not live inside `dispatch()` or route handler bodies.

Example of correct seam:

```python
# core/policy.py — clean function, no framework dependency
def evaluate_policy(tenant_id: str, use_case: str, model_tier: str) -> PolicyDecision:
    ...

# app/middleware/some_middleware.py — thin adapter
async def dispatch(self, request: Request, call_next):
    tenant_id = request.headers.get("X-Tenant-Id", "default")
    decision = evaluate_policy(tenant_id, use_case, model_tier)
    if decision == PolicyDecision.BLOCK:
        return JSONResponse({"detail": "Blocked by policy"}, status_code=403)
    return await call_next(request)
```

Example of incorrect seam (do not do this):

```python
# Wrong: decision logic inside middleware, coupled to Request
async def dispatch(self, request: Request, call_next):
    tenant_id = request.headers.get("X-Tenant-Id", "default")
    if tenant_id in self._blocked_tenants:  # decision logic here, not extractable
        return JSONResponse({"detail": "Blocked"}, status_code=403)
    return await call_next(request)
```

## Envelope discipline

Any new field emitted in JSONL, OTel spans, or metrics must correspond to a named field in
`.kiro/specs/010a-envelope/spec.md`. No parallel semantics should be invented in
middleware-only code. If a field does not exist in the envelope spec, propose adding it to
the spec first, then implement.

## Linting, formatting, and type checking

Run before every commit and in CI:

```bash
ruff check .
ruff format --check .
mypy app/ gateway/ core/ evals/ reporting/ --ignore-missing-imports
```

All three must exit zero.

Ruff configuration (`pyproject.toml`):
```toml
[tool.ruff]
line-length = 100
select = ["E", "F", "I", "UP", "W", "B", "C4"]
ignore = ["B008", "B904"]
exclude = [".venv", ".git", "__pycache__", "artifacts"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

Mypy configuration (`pyproject.toml`):
```toml
[tool.mypy]
python_version = "3.11"
packages = ["app", "gateway", "core", "evals", "reporting"]
ignore_missing_imports = true
warn_return_any = true
warn_unused_ignores = true
warn_unused_configs = true
no_implicit_optional = true
strict_equality = true
```

## Async rules

All gateway calls are async. The full call chain must be async end-to-end:

```
async def route_handler()
  → await call_llm()
    → await _call_provider()
      → await client.responses.create()   # AsyncOpenAI
      → await asyncio.sleep()             # retry backoff only
```

**Never call `time.sleep()` in any async context.** Use `await asyncio.sleep()`.

`app/routes/classify_complexity.py` is the only route that may remain synchronous.

## Type annotations

All public functions must have complete type annotations including return type.

Use `|` union syntax (Python 3.10+), not `Optional[X]` or `Union[X, Y]`.

Use `Literal` for constrained string values.

Use `typing.Protocol` for interfaces (not ABC).

Use `dataclasses.dataclass(frozen=True)` for immutable value objects.

## Module and function design

Modules must be named after concrete responsibilities:
- Good: `token_counter.py`, `circuit_breaker.py`, `cost_model.py`, `envelope.py`
- Bad: `utils.py`, `helpers.py`, `manager.py`, `base.py`, `common.py`

Functions must have one responsibility. Public functions must have docstrings that explain
what the function does, non-obvious behaviour, and error conditions.

## Middleware pattern

Middleware implements `BaseHTTPMiddleware`. Reads configuration from env vars at `__init__`
time. Raises `ValueError` from `__init__` if required vars are missing. Contains no decision
logic — only adapter code that calls clean functions.

## Singleton pattern for stateful gateway components

Cache, circuit breaker, and conversation store each have a module-level singleton. Public
module-level functions delegate to the singleton. Tests reset via the singleton directly.

## Error handling

Use `isinstance` checks against specific exception types from the `openai` package. Never
string-match on error messages.

## Security rules

- Never log API key values.
- Use `secrets.compare_digest()` for API key comparison.
- `APP_API_KEY` must be read from env — never hardcoded.

## Dependency management

- All packages pinned to exact versions in `requirements.txt`.
- `redis` is a soft dependency: import conditionally.
- `numpy` is not a dependency. All vector arithmetic uses pure Python.

## Commenting rules

Comments explain intent or non-obvious constraints — not restate the code.

Upgrade path comments are required in:
1. `RateLimitMiddleware` — Redis upgrade path
2. `InMemoryConversationStore` and `SemanticCache` — Redis upgrade path

Pricing source comment required in `gateway/cost_model.py`:
```python
# Source: https://platform.openai.com/docs/models — retrieved YYYY-MM-DD
```

## Anti-patterns — never introduce these

- `utils.py`, `helpers.py`, or any dumping-ground module
- Base classes for hypothetical future providers
- Abstract factory for the gateway
- Plugin/registry systems
- Deep inheritance hierarchies
- `**kwargs` forwarding through multiple layers
- `Any` type annotations (except where unavoidable with third-party libraries)
- Global mutable state outside the three explicit singletons (cache, circuit breaker, ready flag)
- Synchronous I/O in async route handlers (no `time.sleep`, no synchronous file I/O)
- Hardcoded model names in route handlers (always via `RoutePolicy`)
- Business logic in middleware (only auth and rate limit enforcement)
- Telemetry logic outside `gateway/telemetry.py`
- Decision logic that requires a `Request` object (seam discipline violation)
- New JSONL or span fields not declared in `010a-envelope/spec.md`
