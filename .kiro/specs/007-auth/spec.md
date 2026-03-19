# Spec: 007 Authentication and Rate Limiting

## Goal
Prevent any unauthenticated request from triggering a billable LLM call, and enforce a
per-caller request-per-minute ceiling to prevent runaway billing. Both controls are implemented
as FastAPI middleware so route handlers contain zero auth logic.

## Prerequisite gate
Spec 006 must be complete before starting:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v  # ≥ 138 tests, all passing
curl http://localhost:8000/healthz        # HTTP 200
curl http://localhost:8000/readyz         # HTTP 200 after startup
```

All checks must pass before any task in this spec begins.

## What this spec adds
- `app/middleware/auth.py` — `APIKeyMiddleware`
- `app/middleware/rate_limit.py` — `RateLimitMiddleware`
- `tests/test_auth.py` — auth middleware unit tests
- `tests/test_rate_limit.py` — rate limit middleware unit tests

## What this spec changes
- `app/main.py` — register both middlewares
- `tests/test_routes.py` — add `X-API-Key` header to all existing route test requests

## What this spec does NOT change
Route handlers, gateway, schemas, telemetry, OTel setup, health endpoints, and CI workflows
are frozen.

## Problem
All three API endpoints are fully open. Any HTTP client that can reach the application can
trigger LLM calls billed to the configured `OPENAI_API_KEY`. There is no per-caller identity
and no spending ceiling.

## Acceptance criteria
1. A request to `/answer-routed` without `X-API-Key` returns HTTP 401 `{"detail": "Unauthorized"}`.
2. A request with a wrong `X-API-Key` value returns HTTP 401.
3. A request with the correct `X-API-Key` value proceeds normally.
4. `GET /healthz` without `X-API-Key` returns HTTP 200 (auth-exempt).
5. `GET /readyz` without `X-API-Key` returns HTTP 200 or 503 as appropriate (auth-exempt).
6. A caller that sends more requests per minute than `RATE_LIMIT_RPM` receives HTTP 429 with
   a `Retry-After: 60` header.
7. Two callers with different API keys have independent rate limit counters.
8. The app refuses to start if `APP_API_KEY` is not set — raises `ValueError` from `APIKeyMiddleware.__init__` during middleware setup, before any request is served.
9. The `APP_API_KEY` value is never written to any log output.
10. `OTEL_SDK_DISABLED=true pytest tests/ -v` passes — all existing tests plus new auth/rate-limit tests.

## Testing requirements
- `tests/test_auth.py`: valid key, missing key, wrong key, health endpoint bypass.
- `tests/test_rate_limit.py`: under limit, at limit, over limit, per-key isolation.
  Use `RATE_LIMIT_RPM=3` in test fixtures to keep the window small.
- All existing route tests in `tests/test_routes.py` must pass after adding the required
  `X-API-Key` header to every request fixture.
- Tests inject `APP_API_KEY` via `monkeypatch.setenv` — no real env var setup needed.


## Scope boundary

**This spec implements admission control, not enterprise identity.**

The goal is to protect the demo runtime from anonymous access and to establish clean seams
for future client-specific auth and identity systems. It does not implement JWT, OAuth2,
mTLS, SAML, scoped API key management, or any enterprise identity pattern. Client-specific
implementations replace the seams below without requiring middleware rewrites.

## Hard rules
- Middleware enforced at the middleware layer only — no `Depends()` auth guards in route handlers.
- Exempt paths (`/healthz`, `/readyz`) must be hardcoded in the middleware, not as a config option.
- Rate limit sliding window must use stdlib only (`collections.deque`) — no external packages.
- Middleware registration order in `app/main.py`: `APIKeyMiddleware` executes first
  (runs the auth check before the rate limiter counts the request).
- `APP_API_KEY` must be a required env var. The app must not start without it.
- A comment must document the Redis upgrade path for the rate limiter.

- **Admission seam — pure function, not inline logic.** API key validation must be
  implemented as a pure function defined outside the middleware:

  ```python
  def authenticate(provided_key: str, expected_key: str) -> bool:
      return secrets.compare_digest(provided_key.encode(), expected_key.encode())
  ```

  The middleware is only the adapter that extracts the header value and calls this function.
  This function must be independently testable without instantiating the middleware.

- **Identity/tenant seam — pure function with default fallback.** Caller and tenant
  resolution must be implemented as a pure function outside the middleware:

  ```python
  def resolve_caller(headers: dict[str, str]) -> tuple[str, str]:
      # headers must have lowercase keys (caller normalizes before passing).
      # caller_id: x-api-key value when present, otherwise "default".
      # tenant_id: always "default" in this spec — transport seam for 010a only.
      caller_id = headers.get("x-api-key", "") or "default"
      return (caller_id, "default")
  ```

  The middleware calls this function after successful admission. The returned `caller_id`
  is used as the rate-limit key. The returned `tenant_id` is available for future envelope
  enrichment (spec 010a). In the default implementation, `tenant_id = "default"` is an
  explicit sentinel — not a semantic claim about multi-tenancy.

  `resolve_caller` is deliberately auth-method-agnostic. It does not validate identity;
  it resolves context from whatever headers are available after admission. A client with
  JWT replaces this function to extract claims; the middleware does not change.

- **Non-goal:** This spec does not wire `tenant_id` into the OTel envelope. That
  integration happens in spec 010a when `core/envelope.py` exists. Spec 007 establishes
  the resolution seam; spec 010a consumes it.

- **Hard rule:** `tenant_id = "default"` produced by `resolve_caller` in this spec is a
  transport seam only. It must not be used for any billing, policy, or cost attribution
  logic. Any code that reads `tenant_id` for semantic purposes before spec 010a is a
  scope violation.

- **Hard rule:** `request.state.caller_id` and `request.state.tenant_id` are runtime-local
  convenience values scoped to the current request. They are not part of the core envelope
  contract. The core (spec 010a+) receives resolved values as plain Python arguments, not
  by reading `request.state` directly.
