# Changelog

All notable changes to LLMScope will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-19

### Added

- Gateway-based LLM cost attribution with single choke point (`call_llm()`)
- `ProviderBase` ABC with forward-compatible evolution contract
- Built-in `OpenAIProvider` and `AnthropicProvider` as optional extras
- `LLMRequestEnvelope` — versioned contract for LLM request lifecycle (6 semantic blocks)
- Envelope wired into telemetry data path (JSONL events are envelope serializations)
- OpenTelemetry instrumentation: CLIENT spans with GenAI semantic conventions
- 4 metric instruments: token usage, operation duration, estimated cost, request count
- Semantic convention isolation with dual-emission migration support
- Deterministic cost model with pricing for OpenAI and Anthropic models
- Context management strategies: full, sliding_window, summarized
- Keyword-based routing classifier with documented calibration path
- Dataset-driven eval harness with deterministic mocks
- Markdown reporting from telemetry and eval artifacts
- API key auth middleware with constant-time comparison
- Per-caller sliding window rate limiting
- `src/` layout with PEP 561 `py.typed` marker
- Stability guarantees documented in README

### Provider SDK dependencies

Provider SDKs are optional extras, not hard dependencies:

```bash
pip install llmscope[openai]      # OpenAI only
pip install llmscope[anthropic]   # Anthropic only
pip install llmscope[all]         # Both providers
```

[0.1.0]: https://github.com/lucianareynaud/llmscope/releases/tag/v0.1.0
