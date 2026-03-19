# Tasks: 007 Authentication and Rate Limiting

## Prerequisite gate

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v  # must report ≥ 192 tests, all passing
```

If any test fails, fix it first.

---

## Task 1 — Create app/middleware/ package

- [-] 1.1 Create `app/middleware/__init__.py` (empty)
- [-] 1.2 Verify: `python3 -c "import app.middleware; print('ok')"` succeeds

**Acceptance**: Package importable.

---

## Task 2 — Implement pure functions in app/middleware/auth.py

- [x] 2.1 Create `app/middleware/auth.py`
- [x] 2.2 Add imports: `import logging, os, secrets`, Starlette and FastAPI middleware imports
- [x] 2.3 Define `logger = logging.getLogger(__name__)` at module level
- [ ] 2.4 Implement `authenticate(provided_key: str, expected_key: str) -> bool` as a
  **module-level function** (not inside any class):
  - Body: `return secrets.compare_digest(provided_key.encode("utf-8"), expected_key.encode("utf-8"))`
  - Docstring: explain constant-time comparison and no-logging guarantee
- [ ] 2.5 Implement `resolve_caller(headers: dict[str, str]) -> tuple[str, str]` as a
  **module-level function** (not inside any class):
  - `headers` are expected to have lowercase keys (caller is responsible for normalization)
  - `caller_id = headers.get("x-api-key", "") or "default"`
  - `tenant_id = "default"` always in this spec
  - Docstring: explain that `caller_id` uses the api key value when present; that
    `tenant_id="default"` is a transport seam for spec 010a only, not a business value;
    and that client-specific implementations replace this function without changing the middleware
- [x] 2.6 `ruff check app/middleware/auth.py` — zero errors
- [x] 2.7 `mypy app/middleware/auth.py --ignore-missing-imports` — zero errors

**Acceptance**: `from app.middleware.auth import authenticate, resolve_caller` succeeds.
`authenticate("x", "x")` returns `True`. `authenticate("x", "y")` returns `False`.
`resolve_caller({})` returns `("default", "default")`.
`resolve_caller({"x-api-key": "abc"})` returns `("abc", "default")`.
Both functions testable with no middleware instantiation.

---

## Task 3 — Implement APIKeyMiddleware

- [x] 3.1 Add `APIKeyMiddleware(BaseHTTPMiddleware)` class to `app/middleware/auth.py`
- [x] 3.2 `_EXEMPT_PATHS = {"/healthz", "/readyz"}` as class constant
- [ ] 3.3 `__init__`: read `APP_API_KEY` from `os.environ`; raise `ValueError` if absent or empty.
  This is the single place `APP_API_KEY` is validated — do not add a duplicate check in `app/main.py`.
- [ ] 3.4 `dispatch`:
  - Exempt `_EXEMPT_PATHS` paths immediately
  - `provided = request.headers.get("X-API-Key", "")`
  - Call `authenticate(provided, self._key)` — no inline `compare_digest`
  - On failure: `logger.debug("auth=fail path=%s", request.url.path)`, return 401
  - On success: `logger.debug("auth=ok path=%s", request.url.path)`
  - Normalize headers: `normalized = {k.lower(): v for k, v in request.headers.items()}`
  - Call `resolve_caller(normalized)` to get `(caller_id, tenant_id)`
  - Set `request.state.caller_id = caller_id` and `request.state.tenant_id = tenant_id`
  - Call `await call_next(request)`
- [x] 3.5 `ruff check app/middleware/auth.py` — zero errors
- [x] 3.6 `mypy app/middleware/auth.py --ignore-missing-imports` — zero errors

**Acceptance**: `dispatch` calls `authenticate()` and `resolve_caller()` with no auth
logic inline. `request.state.caller_id` and `request.state.tenant_id` are set after
successful admission. Key value never appears in any log output.

---

## Task 4 — Implement RateLimitMiddleware

- [x] 4.1 Create `app/middleware/rate_limit.py`
- [ ] 4.2 Imports: `import os, time`, `from collections import deque`, Starlette/FastAPI imports
- [ ] 4.3 Implement `RateLimitMiddleware(BaseHTTPMiddleware)`:
  - `_EXEMPT_PATHS = {"/healthz", "/readyz"}` as class constant
  - `__init__`: read `RATE_LIMIT_RPM` from env (default `60`);
    `_windows: dict[str, deque[float]] = {}`
  - Comment: `# For multi-instance deployments, replace deque with Redis ZRANGEBYSCORE + ZADD`
  - `dispatch`:
    - Exempt health paths
    - `caller_id = getattr(request.state, "caller_id", "__anonymous__")`
      (reads from `request.state`, not from headers directly)
    - Evict timestamps older than 60s from the window
    - If `len(window) >= self._rpm`: return 429 with `Retry-After: 60`
    - Else: append `now`, call `await call_next(request)`
- [x] 4.4 `ruff check app/middleware/rate_limit.py` — zero errors

**Acceptance**: Rate window keyed on `request.state.caller_id`. Health paths exempt.
Module imports cleanly.

---

## Task 5 — Register middlewares in app/main.py

- [ ] 5.1 Open `app/main.py`
- [ ] 5.2 Import both middlewares:
  ```python
  from app.middleware.auth import APIKeyMiddleware
  from app.middleware.rate_limit import RateLimitMiddleware
  ```
- [ ] 5.3 Register after `app = FastAPI(...)`:
  ```python
  app.add_middleware(RateLimitMiddleware)  # inner — runs second on request
  app.add_middleware(APIKeyMiddleware)     # outer — runs first on request
  ```
- [ ] 5.4 Do NOT add a duplicate `APP_API_KEY` validation in `app/main.py`.
  The single source of truth is `APIKeyMiddleware.__init__`. Adding a second check
  creates competing error surfaces and complicates test imports.

**Acceptance**: App starts with `APP_API_KEY=test uvicorn app.main:app`.
Without `APP_API_KEY`, raises `ValueError` from `APIKeyMiddleware.__init__` before
any request is served.

---

## Task 6 — Create tests/test_auth.py

- [x] 6.1 Create `tests/test_auth.py`
- [ ] 6.2 Write pure function tests (no TestClient, no middleware):
  - `test_authenticate_correct_key`: `authenticate("secret", "secret")` → `True`
  - `test_authenticate_wrong_key`: `authenticate("wrong", "secret")` → `False`
  - `test_authenticate_empty_provided`: `authenticate("", "secret")` → `False`
  - `test_resolve_caller_no_header`: `resolve_caller({})` → `("default", "default")`
  - `test_resolve_caller_uses_api_key`: `resolve_caller({"x-api-key": "abc"})` → `("abc", "default")`
  - `test_resolve_caller_tenant_always_default`: verify second element is always `"default"`
    regardless of headers present
- [ ] 6.3 Write middleware integration tests (using `TestClient`).
  Use `/classify-complexity` for authenticated pass-through (synchronous, no LLM call,
  no mocks needed). Use `/healthz` for exempt path tests. Do not use `/answer-routed` or
  `/conversation-turn` in auth tests — they require `call_llm` mocks that obscure whether
  the failure is auth or downstream:
  - `test_valid_key_passes`: POST `/classify-complexity` with `X-API-Key: test-key-007` → 200
  - `test_missing_key_rejected`: POST `/classify-complexity` no header → 401, `{"detail": "Unauthorized"}`
  - `test_wrong_key_rejected`: POST `/classify-complexity` with wrong key → 401
  - `test_healthz_exempt`: GET `/healthz` no header → 200
  - `test_readyz_exempt`: GET `/readyz` no header → 200 or 503
- [ ] 6.4 `OTEL_SDK_DISABLED=true pytest tests/test_auth.py -v` — all 11 tests pass

**Acceptance**: 6 pure function tests + 5 middleware tests. Pure function tests require
no TestClient, no running server, no env var setup.

---

## Task 7 — Create tests/test_rate_limit.py

- [ ] 7.1 Create `tests/test_rate_limit.py`
- [ ] 7.2 Add fixture: `RATE_LIMIT_RPM=3` via `monkeypatch.setenv`
- [ ] 7.3 Each test creates a fresh `TestClient` to reset the in-process deque
- [ ] 7.4 Write tests:
  - `test_under_limit_passes`: 2 requests → both 200
  - `test_at_limit_passes`: exactly 3 requests → all 200
  - `test_over_limit_rejected`: 4th request → 429, `Retry-After` header present
  - `test_different_keys_independent`: exhaust limit for key A; key B still succeeds
    (independent windows per `caller_id`)
- [ ] 7.5 `OTEL_SDK_DISABLED=true pytest tests/test_rate_limit.py -v` — all 4 tests pass

**Acceptance**: All 4 pass. Key isolation verified via distinct `X-API-Key` values
(which flow into distinct `caller_id` values via `resolve_caller`).

---

## Task 8 — Update existing route tests

- [ ] 8.1 Confirm `tests/conftest.py` has session-scoped `APP_API_KEY=test-key-007` fixture.
  If not, add it.
- [ ] 8.2 Add `headers={"X-API-Key": "test-key-007"}` to every `client.get()` and
  `client.post()` call in `tests/test_routes.py`
- [ ] 8.3 `OTEL_SDK_DISABLED=true pytest tests/test_routes.py -v` — all existing tests pass

**Acceptance**: No existing assertion logic changes. Only headers added.

---

## Task 9 — Full verification

- [ ] 9.1 `OTEL_SDK_DISABLED=true pytest tests/ -v` — all tests pass (≥ 201)
- [ ] 9.2 `ruff check app/middleware/` — zero errors
- [ ] 9.3 `mypy app/middleware/ --ignore-missing-imports` — zero errors
- [ ] 9.4 Code review check (not grep): open `app/middleware/auth.py` and verify by reading
  that `dispatch()` contains no `compare_digest` call and no direct header equality comparison.
  All credential logic must be inside `authenticate()` and `resolve_caller()`.
- [ ] 9.5 Verify: no log line contains the value of `APP_API_KEY`

**Acceptance**: Full suite green. `dispatch` is a thin adapter.
Auth logic lives exclusively in pure functions.

---

## Completion criteria

- `authenticate()` and `resolve_caller()` exist as module-level pure functions
- `APIKeyMiddleware.dispatch()` calls these functions — no auth logic inline
- `RateLimitMiddleware` uses `request.state.caller_id` — not raw header
- `request.state.tenant_id = "default"` set after admission (seam for spec 010a only)
- No duplicate `APP_API_KEY` validation in `app/main.py`
- 11 auth tests pass (6 pure function + 5 middleware)
- 4 rate limit tests pass
- All existing route tests pass with `X-API-Key` header added
- `OTEL_SDK_DISABLED=true pytest tests/ -v` reports ≥ 201 tests, all passing
- No route handler contains auth or rate-limit logic
