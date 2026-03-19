# Design: 009 Accurate Token Counting with tiktoken

---

## app/services/token_counter.py (new file)

```python
"""BPE token counting for context window management.

Single source of truth for token counting in the application layer.
tiktoken must not be imported anywhere else in the codebase.
"""

import functools

import tiktoken


class ContextTooLargeError(ValueError):
    """Raised when an assembled context exceeds the configured token limit.

    Subclasses ValueError so it is treated as a bad-input condition rather
    than an internal server error. Carries structured attributes so the
    HTTP layer can include exact counts in the 400 response body.
    """

    def __init__(self, actual_tokens: int, max_tokens: int) -> None:
        self.actual_tokens = actual_tokens
        self.max_tokens = max_tokens
        super().__init__(
            f"Context too large: {actual_tokens} tokens exceeds limit of {max_tokens}"
        )


@functools.lru_cache(maxsize=16)
def _get_encoding(model: str) -> tiktoken.Encoding:
    """Return the tiktoken Encoding for `model`, cached per model name.

    Falls back to cl100k_base for unrecognised model identifiers.
    lru_cache prevents repeated disk reads for the BPE vocabulary file,
    which tiktoken downloads on first use and caches locally.
    """
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Return the exact BPE token count for `text` using the vocabulary of `model`.

    Args:
        text:  Input string to tokenise.
        model: Model name used to select the BPE vocabulary. Falls back to
               cl100k_base for unknown identifiers.

    Returns:
        Number of tokens. Returns 0 for empty strings.
    """
    if not text:
        return 0
    return len(_get_encoding(model).encode(text))
```

### Design notes

`ContextTooLargeError` is a `ValueError` subclass. FastAPI does not automatically convert
`ValueError` to HTTP 400 — the route handler must catch it explicitly and raise `HTTPException`.
The structured `actual_tokens` and `max_tokens` attributes allow the 400 response body to
include exact numbers for client-side debugging.

The `if not text: return 0` guard short-circuits before calling the encoder, which avoids
a cache miss for the empty-string edge case and makes the `count_tokens("") == 0` contract
explicit in code rather than dependent on tiktoken's empty-encode behaviour.

---

## app/services/context_manager.py changes

### Removals

- `CHARS_PER_TOKEN = 4` — delete constant
- `_estimate_tokens(text: str) -> int` — delete function entirely

### New imports

```python
import os

from app.services.token_counter import ContextTooLargeError, count_tokens
```

### prepare_context signature

Add `model: str = "gpt-4o"` as the final parameter. Thread it through to every private
helper that calls `count_tokens`. The default preserves backward compatibility for all
existing call sites.

```python
def prepare_context(
    history: list[str],
    message: str,
    strategy: ContextStrategy,
    model: str = "gpt-4o",
) -> tuple[str, int]:
```

### Private helper signatures

Each private helper gains the same `model` parameter and passes it to `count_tokens`:

```python
def _prepare_full_context(
    history: list[str], message: str, model: str
) -> tuple[str, int]:
    messages = history + [message]
    context = _format_messages(messages)
    return context, count_tokens(context, model)


def _prepare_sliding_window_context(
    history: list[str], message: str, model: str
) -> tuple[str, int]:
    recent_history = history[-SLIDING_WINDOW_SIZE:]
    messages = recent_history + [message]
    context = _format_messages(messages)
    return context, count_tokens(context, model)
```

`_prepare_summarized_context` follows the same pattern; every `_estimate_tokens` call is
replaced with `count_tokens(context, model)`.

### MAX_CONTEXT_TOKENS guard

`prepare_context()` assembles the context string via the strategy dispatch, then applies
the guard before returning. The env var is read at call time (not module import time) so
`monkeypatch.setenv` works correctly in tests without module reloads.

```python
def prepare_context(
    history: list[str],
    message: str,
    strategy: ContextStrategy,
    model: str = "gpt-4o",
) -> tuple[str, int]:
    if strategy == "full":
        context, tokens = _prepare_full_context(history, message, model)
    elif strategy == "sliding_window":
        context, tokens = _prepare_sliding_window_context(history, message, model)
    elif strategy == "summarized":
        context, tokens = _prepare_summarized_context(history, message, model)
    else:
        raise ValueError(f"Unknown context strategy: {strategy}")

    max_ctx = int(os.environ.get("MAX_CONTEXT_TOKENS", "8192"))
    if tokens > max_ctx:
        raise ContextTooLargeError(actual_tokens=tokens, max_tokens=max_ctx)

    return context, tokens
```

The strategy dispatch is refactored from `if/return` chains to `if/elif/else` so the guard
runs unconditionally after all strategy branches. The old pattern had `return` inside each
branch, which would have required duplicating the guard in every branch — a maintenance risk.

---

## app/routes/conversation_turn.py changes

The route handler remains `async def` (per spec 008). Wrap the `prepare_context()` call in
try/except. Pass `model="gpt-4o"` explicitly.

```python
from fastapi import APIRouter, HTTPException

from app.schemas.conversation_turn_request import ConversationTurnRequest
from app.schemas.conversation_turn_response import ConversationTurnResponse
from app.services.context_manager import prepare_context
from app.services.token_counter import ContextTooLargeError
from gateway.client import call_llm

router = APIRouter()


@router.post("/conversation-turn", response_model=ConversationTurnResponse)
async def conversation_turn(request: ConversationTurnRequest) -> ConversationTurnResponse:
    """Process a conversation turn with context strategy application."""
    try:
        # prepare_context is synchronous — tiktoken.encode() is CPU-bound with no
        # async interface. Do not add await here.
        prepared_context, context_tokens_used = prepare_context(
            history=request.history,
            message=request.message,
            strategy=request.context_strategy,
            # gpt-4o and gpt-4o-mini share the same BPE vocabulary, so the token
            # count is accurate for both models used by this route's policy.
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

    turn_index = len(request.history)

    result = await call_llm(
        prompt=prepared_context,
        model_tier="expensive",
        route_name="/conversation-turn",
        metadata={
            "conversation_id": request.conversation_id,
            "turn_index": turn_index,
            "context_strategy": request.context_strategy,
            "context_strategy_applied": request.context_strategy,
            "context_tokens_used": context_tokens_used,
        },
    )

    return ConversationTurnResponse(
        answer=result.text,
        turn_index=turn_index,
        context_tokens_used=context_tokens_used,
        context_strategy_applied=request.context_strategy,
    )
```

---

## Test update strategy for tests/test_services.py

Any existing assertion of the form `assert tokens > 0` that relied on the heuristic remaining
positive can stay as-is. Assertions of the form `assert tokens == N` where N was derived from
`len(text) // 4` must be updated to the exact tiktoken value.

**How to obtain exact values:**

```bash
python3 -c "
import tiktoken
enc = tiktoken.encoding_for_model('gpt-4o')

def fmt(messages):
    return '\n'.join(f'Turn {i}: {c}' for i, c in enumerate(messages))

# full, empty history
print('full/empty:', len(enc.encode(fmt(['Hello']))))

# full, [A, B] + Test message
print('full/small:', len(enc.encode(fmt(['A', 'B', 'Test message']))))

# full, [A]*10 + Test message
print('full/large:', len(enc.encode(fmt(['A']*10 + ['Test message']))))

# sliding/long: recent 5 of Message 0..9 + Current message
recent = [f'Message {i}' for i in range(5, 10)]
print('sliding/long:', len(enc.encode(fmt(recent + ['Current message']))))

# full [Message 1, Message 2, Message 3]
print('full/3msgs:', len(enc.encode(fmt(['Message 1', 'Message 2', 'Message 3']))))

# summarized/long (15 history)
old = [f'Message {i}' for i in range(10)]
recent_s = [f'Message {i}' for i in range(10, 15)]
summary = f'[Summary of {len(old)} earlier messages: prior conversation context retained in condensed form]'
ctx = '\n'.join([summary, fmt(recent_s), 'Current: Current message'])
print('summarized/long:', len(enc.encode(ctx)))
"
```

Run this script locally after installing tiktoken and hard-code the output integers as the
expected values in the tests. Add a comment on each assertion showing the input used:

```python
# "Turn 0: Hello" → tiktoken gpt-4o → N tokens
assert tokens == N
```

Do not compute the expected value at test runtime. Do not derive it from `count_tokens()`
inside the test — that would be testing tiktoken against itself, not testing `prepare_context`.

---

## requirements.txt addition

After running `pip install tiktoken` and noting the installed version:

```
tiktoken==<version>   # BPE token counting — imported only in app/services/token_counter.py
```

Pin to the exact version. Add the comment documenting the import constraint.
