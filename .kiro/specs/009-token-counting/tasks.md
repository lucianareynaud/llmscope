# Tasks: 009 Accurate Token Counting with tiktoken

## Prerequisite gate

Run before starting any task:

```bash
OTEL_SDK_DISABLED=true pytest tests/ -v  # ≥ 209 tests, all passing
grep "async def call_llm" gateway/client.py  # must match
```

Both must pass before proceeding.

---

## Task 1 — Add tiktoken to requirements.txt

- [ ] 1.1 Install tiktoken in the project virtualenv:
  ```bash
  pip install tiktoken
  python3 -c "import tiktoken; print(tiktoken.__version__)"
  ```
  Note the printed version string.

- [ ] 1.2 Add to `requirements.txt`, pinned to exact version:
  ```
  tiktoken==<version>   # BPE token counting — imported only in app/services/token_counter.py
  ```

- [ ] 1.3 Verify the import works:
  ```bash
  python3 -c "import tiktoken; print('ok')"
  ```

**Acceptance:** `tiktoken` imports without error. Version is pinned in `requirements.txt`.

---

## Task 2 — Compute exact tiktoken values for test assertions

Run this script **before writing any test file**. The output integers are the hard-coded
expected values for `tests/test_token_counter.py` and `tests/test_services.py`.

```bash
python3 -c "
import tiktoken

enc = tiktoken.encoding_for_model('gpt-4o')

def fmt(messages):
    return '\n'.join(f'Turn {i}: {c}' for i, c in enumerate(messages))

def n(s):
    return len(enc.encode(s)) if s else 0

# token_counter.py tests
print('count_tokens(\"Hello world\") =', n('Hello world'))
print('count_tokens(\"\")            =', n(''))
print('count_tokens(\"a\")           =', n('a'))

# test_services.py: full strategy, empty history
print('full/empty  [\"Hello\"]       =', n(fmt(['Hello'])))

# test_services.py: full strategy, [Message 1, Message 2, Message 3]
print('full/3msgs                 =', n(fmt(['Message 1', 'Message 2', 'Message 3'])))

# test_services.py: token_estimation_grows_with_context
print('growth/empty [Test message]=', n(fmt(['Test message'])))
print('growth/small [A,B,Test]    =', n(fmt(['A', 'B', 'Test message'])))
print('growth/large [A*10,Test]   =', n(fmt(['A']*10 + ['Test message'])))

# test_services.py: sliding_window long history
recent = [f'Message {i}' for i in range(5, 10)]
print('sliding/long               =', n(fmt(recent + ['Current message'])))

# test_services.py: summarized long history (15 items)
old = [f'Message {i}' for i in range(10)]
recent_s = [f'Message {i}' for i in range(10, 15)]
summary = f'[Summary of {len(old)} earlier messages: prior conversation context retained in condensed form]'
ctx = '\n'.join([summary, fmt(recent_s), 'Current: Current message'])
print('summarized/long            =', n(ctx))
"
```

Copy and save all printed values. You will reference them in Tasks 3 and 6.

**Acceptance:** Script runs without error. Values are recorded locally before writing tests.

---

## Task 3 — Create app/services/token_counter.py

- [ ] 3.1 Create `app/services/token_counter.py` with the implementation from `design.md`:
  - Module docstring stating tiktoken must not be imported elsewhere
  - `ContextTooLargeError(ValueError)` with `actual_tokens` and `max_tokens` attributes
  - `_get_encoding(model: str) -> tiktoken.Encoding` decorated with
    `@functools.lru_cache(maxsize=16)`, with `KeyError` fallback to `cl100k_base`
  - `count_tokens(text: str, model: str = "gpt-4o") -> int` with `if not text: return 0`
    guard and docstring

- [ ] 3.2 Verify:
  ```bash
  python3 -c "from app.services.token_counter import count_tokens, ContextTooLargeError; print('ok')"
  ```

- [ ] 3.3 Lint and type-check:
  ```bash
  ruff check app/services/token_counter.py
  mypy app/services/token_counter.py --ignore-missing-imports
  ```
  Both must exit zero.

**Acceptance:** Import works. `ruff` and `mypy` exit zero.

---

## Task 4 — Create tests/test_token_counter.py

Using the values computed in Task 2, create `tests/test_token_counter.py`:

- [ ] 4.1 `test_known_string_exact_count`:
  ```python
  def test_known_string_exact_count():
      # "Hello world" → tiktoken gpt-4o → <VALUE FROM TASK 2> tokens
      assert count_tokens("Hello world", "gpt-4o") == <VALUE>
  ```
  Hard-code the integer. Add the comment showing the input and model.

- [ ] 4.2 `test_empty_string_returns_zero`:
  ```python
  def test_empty_string_returns_zero():
      assert count_tokens("", "gpt-4o") == 0
  ```

- [ ] 4.3 `test_unknown_model_falls_back`:
  ```python
  def test_unknown_model_falls_back():
      # This test verifies only that the fallback path is stable: it does not
      # raise and returns a positive integer. It does NOT assert semantic
      # equivalence of token counts between the fallback encoder and a known
      # model — different vocabularies produce different counts by design.
      # Do not "improve" this test by pinning an exact fallback count.
      known = count_tokens("test", "gpt-4o-mini")
      fallback = count_tokens("test", "nonexistent-model-xyz-999")
      assert isinstance(fallback, int)
      assert fallback > 0
      assert fallback <= known * 3
  ```

- [ ] 4.4 `test_encoder_cached`:
  ```python
  from app.services.token_counter import _get_encoding

  def test_encoder_cached():
      _get_encoding.cache_clear()
      count_tokens("a", "gpt-4o")
      count_tokens("a", "gpt-4o")
      assert _get_encoding.cache_info().hits >= 1
  ```

- [ ] 4.5 Run:
  ```bash
  OTEL_SDK_DISABLED=true pytest tests/test_token_counter.py -v
  ```
  All 4 tests must pass.

**Acceptance:** All 4 tests pass. No assertion uses `// 4` math or calls `count_tokens`
inside the expected-value expression.

---

## Task 5 — Update app/services/context_manager.py

- [ ] 5.1 Remove `CHARS_PER_TOKEN = 4` constant.

- [ ] 5.2 Remove `_estimate_tokens(text: str) -> int` function entirely.

- [ ] 5.3 Add imports at the top of the file:
  ```python
  import os

  from app.services.token_counter import ContextTooLargeError, count_tokens
  ```

- [ ] 5.4 Add `model: str = "gpt-4o"` as the final parameter to `prepare_context()`.

- [ ] 5.5 Thread `model` through to each private helper that computes token counts.
  Each helper signature gains `model: str` as a final parameter.
  Each `_estimate_tokens(context)` call becomes `count_tokens(context, model)`.

- [ ] 5.6 Refactor `prepare_context()` dispatch from `if/return` to `if/elif/else` so
  the `ContextTooLargeError` guard runs unconditionally after all branches:
  ```python
  max_ctx = int(os.environ.get("MAX_CONTEXT_TOKENS", "8192"))
  if tokens > max_ctx:
      raise ContextTooLargeError(actual_tokens=tokens, max_tokens=max_ctx)
  return context, tokens
  ```

- [ ] 5.7 Verify removals:
  ```bash
  grep "CHARS_PER_TOKEN" app/services/context_manager.py   # must return nothing
  grep "_estimate_tokens" app/services/context_manager.py  # must return nothing
  grep "tiktoken" app/services/context_manager.py          # must return nothing
  ```

- [ ] 5.8 Lint and type-check:
  ```bash
  ruff check app/services/context_manager.py
  mypy app/services/context_manager.py --ignore-missing-imports
  ```
  Both must exit zero.

**Acceptance:** All three greps return zero matches. `ruff` and `mypy` exit zero.

---

## Task 6 — Update tests/test_services.py

- [ ] 6.1 Update every assertion on `context_tokens_used` that was derived from `len(text) // 4`
  to the exact integer computed in Task 2. Add a comment on each line showing the input
  and the tiktoken model:
  ```python
  # fmt(["Hello"]) → tiktoken gpt-4o → <N> tokens
  assert tokens == <N>
  ```

- [ ] 6.2 The test `test_token_estimation_grows_with_context` asserts ordering only
  (`tokens_empty < tokens_small < tokens_large`). If those assertions still hold with
  the exact tiktoken values, no change is needed. Verify by running the test.

- [ ] 6.3 Add `test_context_too_large_raises`:
  ```python
  def test_context_too_large_raises(monkeypatch):
      monkeypatch.setenv("MAX_CONTEXT_TOKENS", "10")
      # Any context that produces more than 10 tokens works.
      # "A" * 20 produces well over 10 tokens.
      history = ["A" * 20, "B" * 20]
      message = "C" * 20
      with pytest.raises(ContextTooLargeError) as exc_info:
          prepare_context(history, message, "full")
      assert exc_info.value.actual_tokens > 10
      assert exc_info.value.max_tokens == 10
  ```
  Add the required import at the top of the test file:
  ```python
  from app.services.token_counter import ContextTooLargeError
  ```

- [ ] 6.4 Run:
  ```bash
  OTEL_SDK_DISABLED=true pytest tests/test_services.py -v
  ```
  All tests must pass.

**Acceptance:** No assertion in the file uses `// 4` math. `ContextTooLargeError` test passes.

---

## Task 7 — Update app/routes/conversation_turn.py

- [ ] 7.1 Add `HTTPException` to the FastAPI import:
  ```python
  from fastapi import APIRouter, HTTPException
  ```

- [ ] 7.2 Add the `ContextTooLargeError` import:
  ```python
  from app.services.token_counter import ContextTooLargeError
  ```

- [ ] 7.3 Wrap the `prepare_context()` call in try/except and pass `model="gpt-4o"`:
  ```python
  try:
      prepared_context, context_tokens_used = prepare_context(
          history=request.history,
          message=request.message,
          strategy=request.context_strategy,
          # gpt-4o and gpt-4o-mini share the o200k_base BPE vocabulary.
          # Revisit when multi-vendor provider adapters land in spec 011.
          model="gpt-4o",
      )
  except ContextTooLargeError as exc:
      raise HTTPException(
          status_code=400,
          detail=(
              f"Context too large: {exc.actual_tokens} tokens exceeds "
              f"limit of {exc.max_tokens}"
          ),
      )
  ```
  Note: `prepare_context` is synchronous (no I/O). Do not add `await`.

- [ ] 7.4 Lint:
  ```bash
  ruff check app/routes/conversation_turn.py
  ```
  Must exit zero.

- [ ] 7.5 Verify the HTTP 400 path works via the route test suite (see Task 8).

**Acceptance:** `ContextTooLargeError` is caught and converted to HTTP 400 with structured
message. `prepare_context` is called with `history=request.history` (not a bare `history`
variable). `ruff` exits zero.

---

## Task 8 — Full verification

- [ ] 8.1 Structural checks — all must return zero matches:
  ```bash
  grep -r "CHARS_PER_TOKEN" .              # entire repo
  grep -r "_estimate_tokens" .             # entire repo
  grep -r "tiktoken" app/ --include="*.py" | grep -v token_counter.py  # must be empty
  ```

- [ ] 8.2 Full test suite:
  ```bash
  OTEL_SDK_DISABLED=true pytest tests/ -v
  ```
  All tests must pass. Count must be ≥ 217.

- [ ] 8.3 Lint and type-check all modified packages:
  ```bash
  ruff check app/ gateway/ evals/ reporting/
  ruff format --check app/ gateway/ evals/ reporting/
  mypy app/ gateway/ evals/ reporting/ --ignore-missing-imports
  ```
  All three must exit zero.

- [ ] 8.4 Seam check — verify `token_counter.py` has no framework coupling:
  ```bash
  grep -n "fastapi\|starlette\|Request\|Response" app/services/token_counter.py
  ```
  Must return nothing. `count_tokens` must be callable from a plain Python script with no
  FastAPI installed.

**Acceptance:** All structural checks pass. Full test suite green at ≥ 217 tests.
`ruff`, `ruff format`, and `mypy` exit zero.

---

## Completion criteria

This spec is complete when:

- `CHARS_PER_TOKEN` and `_estimate_tokens` are absent from the entire codebase
- `tiktoken` is imported only in `app/services/token_counter.py`
- `count_tokens` uses BPE encoding with `lru_cache`
- `ContextTooLargeError` is raised by `prepare_context()` and caught with HTTP 400
- All token count assertions in tests use exact tiktoken integers, hard-coded with comments
- `OTEL_SDK_DISABLED=true pytest tests/ -v` reports ≥ 217 tests, all passing
- `ruff check`, `ruff format --check`, and `mypy` exit zero across all packages
