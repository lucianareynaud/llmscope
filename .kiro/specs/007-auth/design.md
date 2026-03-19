# Design: 007 Authentication and Rate Limiting

## Scope reminder

This spec implements admission control, not enterprise identity. Two pure functions
establish the seams for future client-specific implementations. The middleware is only
the adapter that calls them.

`APIKeyMiddleware` is named for precision in this spec. It may be renamed to
`AdmissionMiddleware` in a future refactor if the validation method changes — the seam
design makes that rename trivially safe.

---

## Pure functions (app/middleware/auth.py)

These two functions are defined at module level, outside any class. They are the seams.
Both are independently testable without middleware instantiation.

### `authenticate`

```python
import secrets

def authenticate(provided_key: str, expected_key: str) -> bool:
    """Return True if provided_key matches expected_key.

    Uses secrets.compare_digest for constant-time comparison to prevent
    timing-based key enumeration attacks. Neither key value is ever logged.
    """
    return secrets.compare_digest(
        provided_key.encode("utf-8"),
        expected_key.encode("utf-8"),
    )
```

### `resolve_caller`

```python
def resolve_caller(headers: dict[str, str]) -> tuple[str, str]:
    """Resolve (caller_id, tenant_id) from normalized request headers.

    Args:
        headers: dict with header names normalized to lowercase.

    Returns:
        (caller_id, tenant_id) where:
        - caller_id: the x-api-key value when present, otherwise "default".
          Used as the rate-limit window key. Not an identity claim.
        - tenant_id: always "default" in this spec. This is a transport seam
          for spec 010a — not a multi-tenancy claim and not suitable for
          billing, policy, or cost attribution until 010a wires it properly.

    Client-specific implementations replace this function to extract identity
    from JWT claims, OAuth tokens, or other auth artifacts. The middleware
    does not change when this function is replaced.

    This function is auth-method-agnostic: it resolves context from available
    headers after admission, it does not validate identity.
    """
    # Normalize: headers must arrive lowercase. The middleware passes
    # {k.lower(): v for k, v in request.headers.items()} to ensure this.
    caller_id = headers.get("x-api-key", "") or "default"
    return (caller_id, "default")
```

**Header normalization rule:** `resolve_caller` expects lowercase header names.
The middleware is responsible for normalizing before calling. This makes
`resolve_caller` testable with plain dicts and avoids case-sensitivity bugs.

---

## APIKeyMiddleware (app/middleware/auth.py)

```python
class APIKeyMiddleware(BaseHTTPMiddleware):
    _EXEMPT_PATHS = {"/healthz", "/readyz"}

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        key = os.environ.get("APP_API_KEY")
        if not key:
            raise ValueError("APP_API_KEY environment variable is required")
        self._key = key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self._EXEMPT_PATHS:
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")

        if not authenticate(provided, self._key):
            logger.debug("auth=fail path=%s", request.url.path)
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        logger.debug("auth=ok path=%s", request.url.path)

        # Normalize headers to lowercase before passing to resolve_caller.
        normalized = {k.lower(): v for k, v in request.headers.items()}
        caller_id, tenant_id = resolve_caller(normalized)

        # Store as runtime-local convenience for RateLimitMiddleware.
        # These are NOT core envelope fields — spec 010a receives resolved
        # values as plain Python arguments, not by reading request.state.
        request.state.caller_id = caller_id
        request.state.tenant_id = tenant_id

        return await call_next(request)
```

`dispatch` contains no auth logic. It calls `authenticate()` and `resolve_caller()`
and acts on their results. Replacing either function changes behavior without touching
`dispatch`.

---

## RateLimitMiddleware (app/middleware/rate_limit.py)

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    _EXEMPT_PATHS = {"/healthz", "/readyz"}

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._rpm: int = int(os.environ.get("RATE_LIMIT_RPM", "60"))
        self._windows: dict[str, deque[float]] = {}
        # For multi-instance deployments, replace deque with Redis ZRANGEBYSCORE + ZADD

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self._EXEMPT_PATHS:
            return await call_next(request)

        # Read caller_id set by APIKeyMiddleware after admission.
        # Falls back to "__anonymous__" defensively — should not occur
        # given registration order, but guards against future middleware reordering.
        caller_id = getattr(request.state, "caller_id", "__anonymous__")

        now = time.monotonic()
        window = self._windows.setdefault(caller_id, deque())

        while window and now - window[0] > 60.0:
            window.popleft()

        if len(window) >= self._rpm:
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )

        window.append(now)
        return await call_next(request)
```

---

## Middleware registration order in app/main.py

```python
# Rate limit second → inner wrapper → runs second on request
app.add_middleware(RateLimitMiddleware)
# Auth first → outer wrapper → runs first on request
app.add_middleware(APIKeyMiddleware)
```

`APP_API_KEY` validation happens inside `APIKeyMiddleware.__init__` — that is the single
source of truth for this requirement. No duplicate validation in `app/main.py` module scope.

---

## Test patterns

### Pure function tests — no middleware, no TestClient

```python
from app.middleware.auth import authenticate, resolve_caller

def test_authenticate_correct_key():
    assert authenticate("secret", "secret") is True

def test_authenticate_wrong_key():
    assert authenticate("wrong", "secret") is False

def test_resolve_caller_no_header():
    caller_id, tenant_id = resolve_caller({})
    assert caller_id == "default"
    assert tenant_id == "default"

def test_resolve_caller_uses_api_key():
    caller_id, tenant_id = resolve_caller({"x-api-key": "key-abc"})
    assert caller_id == "key-abc"
    assert tenant_id == "default"
```

### Middleware integration tests — use /healthz and /classify-complexity carefully

For admission control tests, prefer `/healthz` (exempt path, no downstream logic) and
`/classify-complexity` (synchronous, no LLM call, no mocks needed). Avoid `/answer-routed`
and `/conversation-turn` in auth tests — they require `call_llm` mocks that obscure
whether the test is about auth or about downstream behavior.

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=True)

def test_valid_key_passes():
    response = client.post(
        "/classify-complexity",
        json={"message": "hi"},
        headers={"X-API-Key": "test-key-007"},
    )
    assert response.status_code == 200

def test_missing_key_rejected():
    response = client.post("/classify-complexity", json={"message": "hi"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
```
