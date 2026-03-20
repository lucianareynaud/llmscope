# Testing Steering

## Test execution command

All tests must be run with OTel disabled:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v
```

Never run `pytest` without `OTEL_SDK_DISABLED=true`. OTel startup in tests causes background
threads, exporter connection attempts, and intermittent teardown failures.

## pytest configuration (pyproject.toml)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

`asyncio_mode = "auto"` makes every `async def test_...` function run as an asyncio test
without `@pytest.mark.asyncio` decorators. Do not add decorators — they are redundant with
this setting.

---

## Async test pattern

All test functions that call `async def` code must themselves be `async def`:

```python
# Correct
async def test_call_llm_returns_result():
    with patch("gateway.client._call_provider", new_callable=AsyncMock) as mock_provider:
        mock_provider.return_value = ("response text", 10, 20)
        result = await call_llm("Hello", "/answer-routed")
    assert result.text == "response text"

# Wrong — will hang or fail silently
def test_call_llm_returns_result():
    result = call_llm("Hello", "/answer-routed")  # returns a coroutine, not a result
```

---

## Mocking async functions — use AsyncMock

When patching a function that is `async def`, always use `AsyncMock`, not `Mock`:

```python
from unittest.mock import AsyncMock, patch

# Correct
with patch("app.routes.answer_routed.call_llm", new_callable=AsyncMock) as mock_llm:
    mock_llm.return_value = GatewayResult(text="answer", ...)
    response = client.post("/answer-routed", ...)

# Wrong — regular Mock cannot be awaited
with patch("app.routes.answer_routed.call_llm") as mock_llm:
    mock_llm.return_value = GatewayResult(...)  # TypeError: object is not awaitable
```

`TestClient` handles async route handlers transparently — use `AsyncMock` only for the patch,
not for the test client.

---

## Mocking OpenAI errors

Use real `openai.*Error` classes, not plain `Exception`:

```python
import httpx, openai

def _make_rate_limit_error() -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com"))
    return openai.RateLimitError("rate limited", response=response, body={})
```

The gateway uses `isinstance(error, openai.RateLimitError)` — a plain `Exception("rate limited")`
will not match and will be classified as `"unknown"`.

---

## Environment variable injection

Use `monkeypatch.setenv()`. Never set env vars globally in module scope:

```python
# Correct — scoped to the test
def test_auth_valid(monkeypatch):
    monkeypatch.setenv("APP_API_KEY", "test-key-123")

# Wrong — bleeds into other tests
os.environ["APP_API_KEY"] = "test-key-123"
```

The session-scoped `conftest.py` fixture sets `APP_API_KEY` for the entire test session.
Individual tests use `monkeypatch` for transient overrides only.

---

## State reset between tests

Module-level singletons hold state between tests. Always reset in an `autouse` fixture:

```python
@pytest.fixture(autouse=True)
def reset_gateway_state():
    cache_clear()
    circuit_force_state("closed")
    yield
```

---

## Testing rate limiting

Set `RATE_LIMIT_RPM` to a small number and create a fresh `TestClient` per test:

```python
@pytest.fixture(autouse=True)
def rate_limit_env(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_RPM", "3")
```

---

## What to test vs. what to mock

### Mock at the OpenAI boundary only

- Gateway tests: mock `_call_provider` (not `call_llm`)
- Route tests: mock `call_llm` with `AsyncMock`
- Routing tests: mock `openai.embeddings.create`
- Cache semantic tests: mock `_cache._embed`

### Do not mock internal business logic

Do not mock `determine_complexity`, `prepare_context`, or `count_tokens` — these are pure
functions with no I/O.

### Assert on behaviour, not implementation

```python
# Correct
assert result.selected_model == "gpt-4o-mini"
assert response.status_code == 200

# Wrong
assert mock_client.responses.create.call_count == 1
```

### Testing envelope fields

When a new spec adds envelope fields to telemetry, tests must assert on those fields:

```python
# Correct — test that cost_source is set correctly
def test_cache_hit_has_cached_zero_cost_source(span_exporter, mock_cache_hit):
    call_llm("hello", "cheap", "/answer-routed")
    spans = span_exporter.get_finished_spans()
    assert spans[0].attributes["llmscope.cost_source"] == "cached_zero"
    assert spans[0].attributes["llmscope.estimated_cost_usd"] == 0.0
```

Never assert on hardcoded field values that bypass `llmscope/semconv.py` constants. Always use
the constant:

```python
from core.semconv import ATTR_LLMSCOPE_COST_SOURCE
assert spans[0].attributes[ATTR_LLMSCOPE_COST_SOURCE] == "cached_zero"
```

---

## Dependency override pattern for FastAPI Depends()

```python
@pytest.fixture(autouse=True)
def override_conversation_store():
    store = InMemoryConversationStore()
    app.dependency_overrides[get_conversation_store] = lambda: store
    yield store
    app.dependency_overrides.clear()  # mandatory teardown
```

---

## Test file organisation

Each component gets its own test file:

| Component | Test file |
|---|---|
| Envelope schema and enums | `tests/test_envelope.py` |
| Auth middleware | `tests/test_auth.py` |
| Rate limit middleware | `tests/test_rate_limit.py` |
| Token counter | `tests/test_token_counter.py` |
| Exact cache | `tests/test_cache.py` |
| Circuit breaker | `tests/test_circuit_breaker.py` |
| Conversation store | `tests/test_conversation_store.py` |
| Gateway (call_llm, errors, retry) | `tests/test_gateway.py` |
| Routes | `tests/test_routes.py` |
| Services (routing, context) | `tests/test_services.py` |
| OTel semconv constants | `tests/test_semconv.py` |

Do not add tests for new components to `test_gateway.py` or `test_routes.py`.

---

## Minimum test coverage per component

| Component | Minimum tests |
|---|---|
| Envelope (`test_envelope.py`) | valid construction, required field enforcement, all enum values instantiable, `cost_source=cached_zero` implies `estimated_cost_usd=0` |
| Auth middleware | valid key, missing key, wrong key, health bypass ×2 |
| Rate limit | under limit, at limit, over limit, two-key isolation |
| Token counter | exact count for known string, empty string, unknown model fallback, lru_cache reuse |
| Exact cache | exact hit, miss, stats hit/miss counters, clear, cross-model isolation, bypass when disabled, `cost_source=cached_zero` in telemetry |
| Circuit breaker | initial closed, threshold opens, check raises when open, timeout → half-open, success closes, failure reopens, thread safety |
| Conversation store | get unknown → `[]`, append+get order preserved, delete, TTL expiry, two IDs isolated, Redis with fakeredis |
| Routing | correct tuple type with mocked embeddings, fallback on API error, cosine identical=1.0, kNN majority vote |

---

## Test count tracking

Test counts are approximate targets, not hard gates. The gate is zero failures, not a specific
count. If a spec produces fewer or more tests than estimated, investigate intent before
adjusting expectations.

Approximate count after each core track spec:

| After spec | Approximate total |
|---|---|
| 001–006 (baseline) | ~192 |
| 007 (auth) | ~201 |
| 008 (async retry) | ~209 |
| 009 (token counting) | ~217 |
| 010a (envelope) | ~225 |
| 010 (cache revised) | ~233 |
| 011 (core extraction) | ~241 |

Gateway runtime track specs (012–014) add tests to their own files independently of the core
count. They do not have count gates.
