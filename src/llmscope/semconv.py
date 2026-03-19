"""
LLMScope semantic conventions — project-specific attribute names.

This module defines the `llmscope.*` namespace constants for attributes
that are not covered by official OpenTelemetry semantic conventions.

Use official `gen_ai.*` semconv from gateway/semconv.py for standard fields.
"""

# Schema versioning
ATTR_LLMSCOPE_SCHEMA_VERSION = "llmscope.schema_version"

# Economics
ATTR_LLMSCOPE_COST_SOURCE = "llmscope.cost_source"
ATTR_LLMSCOPE_ESTIMATED_COST_USD = "llmscope.estimated_cost_usd"
ATTR_LLMSCOPE_TOKENS_TOTAL = "llmscope.tokens_total"

# Identity and context
ATTR_LLMSCOPE_REQUEST_ID = "llmscope.request_id"
ATTR_LLMSCOPE_TENANT_ID = "llmscope.tenant_id"
ATTR_LLMSCOPE_CALLER_ID = "llmscope.caller_id"
ATTR_LLMSCOPE_USE_CASE = "llmscope.use_case"
ATTR_LLMSCOPE_ROUTE = "llmscope.route"
ATTR_LLMSCOPE_RUNTIME_MODE = "llmscope.runtime_mode"

# Model selection and routing
ATTR_LLMSCOPE_MODEL_TIER = "llmscope.model_tier"
ATTR_LLMSCOPE_ROUTING_DECISION = "llmscope.routing_decision"
ATTR_LLMSCOPE_ROUTING_REASON = "llmscope.routing_reason"

# Reliability
ATTR_LLMSCOPE_STATUS = "llmscope.status"
ATTR_LLMSCOPE_ERROR_TYPE = "llmscope.error_type"
ATTR_LLMSCOPE_LATENCY_MS = "llmscope.latency_ms"
ATTR_LLMSCOPE_RETRY_COUNT = "llmscope.retry_count"
ATTR_LLMSCOPE_FALLBACK_TRIGGERED = "llmscope.fallback_triggered"
ATTR_LLMSCOPE_FALLBACK_REASON = "llmscope.fallback_reason"
ATTR_LLMSCOPE_CIRCUIT_STATE = "llmscope.circuit_state"

# Governance
ATTR_LLMSCOPE_POLICY_INPUT_CLASS = "llmscope.policy_input_class"
ATTR_LLMSCOPE_POLICY_DECISION = "llmscope.policy_decision"
ATTR_LLMSCOPE_POLICY_MODE = "llmscope.policy_mode"
ATTR_LLMSCOPE_REDACTION_APPLIED = "llmscope.redaction_applied"
ATTR_LLMSCOPE_PII_DETECTED = "llmscope.pii_detected"

# Cache and evaluation
ATTR_LLMSCOPE_CACHE_ELIGIBLE = "llmscope.cache_eligible"
ATTR_LLMSCOPE_CACHE_STRATEGY = "llmscope.cache_strategy"
ATTR_LLMSCOPE_CACHE_HIT = "llmscope.cache_hit"
ATTR_LLMSCOPE_CACHE_KEY_FINGERPRINT = "llmscope.cache_key_fingerprint"
ATTR_LLMSCOPE_CACHE_KEY_ALGORITHM = "llmscope.cache_key_algorithm"
ATTR_LLMSCOPE_CACHE_LOOKUP_CONFIDENCE = "llmscope.cache_lookup_confidence"
ATTR_LLMSCOPE_EVAL_HOOKS = "llmscope.eval_hooks"
