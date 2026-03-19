# Spec: 008 Async-Safe Gateway Retry

## Goal
Replace the synchronous `time.sleep()` in the gateway retry loop with `asyncio.sleep()` so
that backoff during retries does not block the FastAPI event loop. This is a correctness fix —
under load, a single 2-second synchronous sleep stalls every concurrent request on that worker.

## Prerequisite gate
Spec 007 must be complete before starting:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v  # ≥ 201 tests, all passing
```

All tests must be green before any task in this spec begins.

## What this spec changes
- `gateway/client.py` — `call_llm` and `_call_provider` become `async def`; `AsyncOpenAI` replaces `OpenAI`; `asyncio.sleep` replaces `time.sleep`
- `app/routes/answer_routed.py` — route handler becomes `async def`
- `app/routes/conversation_turn.py` — route handler becomes `async def`
- `tests/test_gateway.py` — async test functions, `AsyncMock` for gateway mocks
- `tests/test_routes.py` — `AsyncMock` where `call_llm` is patched

## What this spec does NOT change
The retry logic, backoff formula, max retries, OTel spans, telemetry emission, error
classification, cost model, schemas, middleware, and health endpoints are all frozen.

## Problem
`gateway/client.py._call_provider()` calls `time.sleep(2**attempt)` before each retry.
FastAPI's ASGI server (uvicorn) runs a single event loop per worker. A synchronous sleep
inside a route handler (even if the route is declared `async`) blocks the event loop thread,
preventing all other concurrent requests from making progress during the sleep window.
With a 3-retry policy and 2-second base backoff, a single failing request can block the
event loop for up to 14 seconds (2 + 4 + 8).

## Acceptance criteria
1. `time.sleep` does not appear anywhere in `gateway/client.py`.
2. `asyncio.sleep` is used in the retry path.
3. `call_llm` is declared `async def`.
4. `_call_provider` is declared `async def`.
5. `app/routes/answer_routed.py` route handler is `async def`.
6. `app/routes/conversation_turn.py` route handler is `async def`.
7. `AsyncOpenAI` is used instead of `OpenAI` for the provider client.
8. All gateway tests that call `call_llm` are `async def`. No `@pytest.mark.asyncio` decorators are added — the project must use `asyncio_mode = "auto"` (set in Task 1 if not already present).
9. All route tests that patch `call_llm` use `AsyncMock`.
10. `OTEL_SDK_DISABLED=true pytest tests/ -v` — all tests pass.

## Testing requirements
- No new test files are created. Existing tests are updated.
- Tests that directly call `call_llm(...)` become `async def`. No `@pytest.mark.asyncio` decorator — `asyncio_mode = "auto"` must be present in `pyproject.toml` (Task 1 ensures this) and applies automatically.
- Tests that patch `call_llm` at the route layer must use `AsyncMock` instead of `Mock`.
- Before adding `pytest-asyncio` to `requirements.txt`, verify it is not already present. Add only if absent.
- Before adding `asyncio_mode = "auto"` to `pyproject.toml`, verify it is not already configured. Add only if absent.
- Do not add `@pytest.mark.asyncio` decorators. They are redundant with `asyncio_mode = "auto"` and the project coding standards prohibit them.

## Hard rules
- `classify_complexity` route stays synchronous — it does not call the gateway. No other route handler should be converted to async as part of this spec unless it already calls `call_llm`. Do not normalize handlers for aesthetic consistency.
- `import time` must remain — `time.perf_counter()` is still used for latency measurement.
- Only `time.sleep` is removed; `time.perf_counter` is kept.
- No retry logic changes — only the sleep implementation changes.
- The OTel span structure inside `call_llm` is unchanged.
- Seam discipline: `call_llm` signature does not change — only `def` → `async def`. All callers simply add `await`. This spec introduces no new fields, no new semantics, and no new framework coupling. No `Request`, `Response`, dependency injection, or route-layer context objects may be threaded into `call_llm` or `_call_provider` as part of this conversion.
