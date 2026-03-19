# Tasks: 008 Async-Safe Gateway Retry

## Prerequisite gate
Run before starting any task:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v  # must report ≥ 201 tests, all passing
```

If any test fails, fix it before proceeding.

---

## Task 1 — Verify or add pytest-asyncio

- [ ] 1.1 Check if `pytest-asyncio` is already in `requirements.txt`:
  ```bash
  grep "pytest-asyncio" requirements.txt
  ```
  If present, skip 1.2. If absent, proceed to 1.2.
- [ ] 1.2 (If absent) Add `pytest-asyncio` to `requirements.txt` with an explicitly pinned stable version consistent with the project's dependency policy (check PyPI for latest stable, do not derive the version from the local environment)
- [ ] 1.3 Check if `asyncio_mode = "auto"` is already in `pyproject.toml`:
  ```bash
  grep "asyncio_mode" pyproject.toml
  ```
  If present, skip 1.4. If absent, add it.
- [ ] 1.4 (If absent) Add to `pyproject.toml` under `[tool.pytest.ini_options]`:
  ```toml
  asyncio_mode = "auto"
  ```

**Acceptance**: `python3 -c "import pytest_asyncio; print('ok')"` succeeds.
`asyncio_mode = "auto"` is present in `pyproject.toml`.

---

## Task 2 — Convert gateway/client.py to async

- [ ] 2.1 Confirm `AsyncOpenAI` is available in the installed package:
  ```bash
  python3 -c "from openai import AsyncOpenAI; print('ok')"
  ```
  If this fails, check the `openai` version in `requirements.txt` before proceeding.
- [ ] 2.2 Open `gateway/client.py`
- [ ] 2.3 Replace `from openai import OpenAI` with `from openai import AsyncOpenAI`
- [ ] 2.5 Replace `OpenAI(api_key=...)` instantiation with `AsyncOpenAI(api_key=...)`
- [ ] 2.6 Add `import asyncio` at the top of the file
- [ ] 2.7 Change `def call_llm(...)` to `async def call_llm(...)`
- [ ] 2.8 Change `def _call_provider(...)` to `async def _call_provider(...)`
- [ ] 2.9 Replace `client.responses.create(...)` with `await client.responses.create(...)`
- [ ] 2.10 Replace `time.sleep(2**attempt)` with `await asyncio.sleep(2**attempt)`
- [ ] 2.11 Confirm `import time` is still present (used by `time.perf_counter()`)
- [ ] 2.12 Confirm `time.sleep` does not appear anywhere in the file:
  ```bash
  grep "time\.sleep" gateway/client.py  # must return zero matches
  ```
- [ ] 2.13 `ruff check gateway/client.py` — zero errors
- [ ] 2.14 `mypy gateway/client.py --ignore-missing-imports` — zero errors

**Acceptance**: `gateway/client.py` contains `async def call_llm` and `async def _call_provider`.
`asyncio.sleep` is in the retry path. `time.sleep` is gone.

---

## Task 3 — Update app/routes/answer_routed.py

- [ ] 3.1 Open `app/routes/answer_routed.py`
- [ ] 3.2 Change `def answer_routed(...)` to `async def answer_routed(...)`
- [ ] 3.3 Change `result = call_llm(...)` to `result = await call_llm(...)`
- [ ] 3.4 `ruff check app/routes/answer_routed.py` — zero errors

**Acceptance**: Route function is `async def`. `await call_llm(...)` is used.

---

## Task 4 — Update app/routes/conversation_turn.py

- [ ] 4.1 Open `app/routes/conversation_turn.py`
- [ ] 4.2 Change `def conversation_turn(...)` to `async def conversation_turn(...)`
- [ ] 4.3 Change `result = call_llm(...)` to `result = await call_llm(...)`
- [ ] 4.4 `ruff check app/routes/conversation_turn.py` — zero errors

**Acceptance**: Route function is `async def`. `await call_llm(...)` is used.

---

## Task 5 — Update tests/test_gateway.py

- [ ] 5.1 Open `tests/test_gateway.py`
- [ ] 5.2 Find all test functions that directly call `call_llm(...)`:
  - Change each `def test_...()` to `async def test_...()`
  - Change each `call_llm(...)` to `await call_llm(...)`
  - Do NOT add `@pytest.mark.asyncio` decorators — `asyncio_mode = "auto"` is already in `pyproject.toml`
- [ ] 5.3 Replace mocks of awaited coroutine call sites with `AsyncMock`:
  - If `_call_provider` or `client.responses.create` is patched, use `AsyncMock` — these are awaited coroutines
  - If a mock represents a plain return value object (e.g., a `GatewayResult`), keep plain `Mock` — it is not awaited
  - Add `from unittest.mock import AsyncMock` if not already imported
  - Do not replace every `Mock` with `AsyncMock` indiscriminately
- [ ] 5.4 Run: `OTEL_SDK_DISABLED=true pytest tests/test_gateway.py -v` — all gateway tests pass

**Acceptance**: All tests in `test_gateway.py` pass. No `call_llm(...)` without `await`.

---

## Task 6 — Update tests/test_routes.py

- [ ] 6.1 Open `tests/test_routes.py`
- [ ] 6.2 Find all `patch("app.routes.answer_routed.call_llm", ...)` usages:
  - Add `new_callable=AsyncMock` to each patch call — this patches the async function itself, not its return value
  - Pattern: `patch("app.routes.answer_routed.call_llm", new_callable=AsyncMock, return_value=mock_result)`
- [ ] 6.3 Find all `patch("app.routes.conversation_turn.call_llm", ...)` usages:
  - Add `new_callable=AsyncMock` to each patch call (same pattern)
- [ ] 6.4 Run: `OTEL_SDK_DISABLED=true pytest tests/test_routes.py -v` — all route tests pass

**Acceptance**: All tests in `test_routes.py` pass. `AsyncMock` used for all `call_llm` patches.

---

## Task 7 — Full verification

- [ ] 7.1 `grep "time\.sleep" gateway/client.py` — zero matches
- [ ] 7.2 `grep "asyncio\.sleep" gateway/client.py` — at least one match (the retry path)
- [ ] 7.3 `grep "async def call_llm" gateway/client.py` — one match
- [ ] 7.4 `grep "async def answer_routed" app/routes/answer_routed.py` — one match
- [ ] 7.5 `grep "async def conversation_turn" app/routes/conversation_turn.py` — one match
- [ ] 7.6 `OTEL_SDK_DISABLED=true pytest tests/ -v` — all tests pass, ≥ 201 tests

**Acceptance**: All structural checks pass. Full test suite green.

---

## Completion criteria
This spec is complete when:
- `time.sleep` is absent from `gateway/client.py`
- `asyncio.sleep` is used in the retry backoff path
- `call_llm` and `_call_provider` are `async def`
- Both LLM-calling route handlers are `async def`
- `AsyncOpenAI` is the provider client
- `OTEL_SDK_DISABLED=true pytest tests/ -v` reports ≥ 201 tests, all passing
- `ruff check` and `mypy` pass on all modified files
