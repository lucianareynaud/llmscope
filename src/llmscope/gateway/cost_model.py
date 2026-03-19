"""Deterministic cost estimation from local pricing configuration.

This module owns cost estimation for gateway-backed provider calls based on
token counts and local inspectable pricing data.

PRICING CONVENTION
───────────────────
Model keys use the format returned by the provider SDK (e.g. ``"gpt-4o-mini"``,
``"claude-sonnet-4-20250514"``). This must match the ``model`` argument passed
to ``estimate_cost()`` by ``gateway/client.py``.

Pricing is intentionally hardcoded as a local snapshot for reproducibility and
auditability. In production, replace with a YAML/JSON file or an API lookup,
keeping the same ``estimate_cost()`` interface.
"""

from copy import deepcopy

TOKENS_PER_MILLION = 1_000_000

# ─────────────────────────────────────────────────────────────────────────────
# Local pricing snapshot
#
# Sources:
#   OpenAI:    https://platform.openai.com/docs/models — retrieved 2026-02-28
#   Anthropic: https://docs.anthropic.com/en/docs/about-claude/models — retrieved 2026-03-19
#
# Prices are in USD per 1M tokens (standard tier, non-cached input).
# ─────────────────────────────────────────────────────────────────────────────

MODEL_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o-mini": {
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
    },
    "gpt-4o": {
        "input_per_1m": 2.50,
        "output_per_1m": 10.00,
    },
    # Anthropic
    "claude-sonnet-4-20250514": {
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
    },
    "claude-haiku-3-5-20241022": {
        "input_per_1m": 0.80,
        "output_per_1m": 4.00,
    },
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate request cost from model and token counts.

    Cost is calculated as:

        (tokens_in / 1_000_000 * input_per_1m)
        + (tokens_out / 1_000_000 * output_per_1m)

    Args:
        model: Resolved model name (must match a key in MODEL_PRICING).
        tokens_in: Number of input tokens.
        tokens_out: Number of output tokens.

    Returns:
        Estimated cost in USD.

    Raises:
        ValueError: If the model is unknown or token counts are negative.
    """
    if model not in MODEL_PRICING:
        raise ValueError(f"Unknown model for cost estimation: {model}")

    if tokens_in < 0 or tokens_out < 0:
        raise ValueError(
            f"Token counts must be non-negative. Got tokens_in={tokens_in}, "
            f"tokens_out={tokens_out}."
        )

    pricing = MODEL_PRICING[model]

    input_cost = (tokens_in / TOKENS_PER_MILLION) * pricing["input_per_1m"]
    output_cost = (tokens_out / TOKENS_PER_MILLION) * pricing["output_per_1m"]

    return input_cost + output_cost


def get_pricing() -> dict[str, dict[str, float]]:
    """Return a safe copy of the current local pricing configuration."""
    return deepcopy(MODEL_PRICING)
