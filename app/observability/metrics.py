"""Prometheus metrics.

Label cardinality is kept deliberately low: route templates rather than raw
paths, rule ids (a closed set from the rule packs), and normalised guardrail
violation slugs.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from prometheus_client import Counter, Gauge, Histogram, Info

from app import ENGINE_VERSION, __version__

# --------------------------------------------------------------------------
# Build / configuration info
# --------------------------------------------------------------------------
app_info = Info("demobank_app", "Build and configuration of the assistant")

policy_index_chunks = Gauge(
    "demobank_policy_index_chunks",
    "Number of indexed policy chunks per policy version",
    ["policy_version"],
)

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
http_requests_total = Counter(
    "demobank_http_requests_total",
    "HTTP requests handled",
    ["method", "route", "status"],
)

http_request_duration_seconds = Histogram(
    "demobank_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

http_exceptions_total = Counter(
    "demobank_http_exceptions_total",
    "Unhandled exceptions raised while handling a request",
    ["route"],
)

# --------------------------------------------------------------------------
# Rule engine
# --------------------------------------------------------------------------
eligibility_decisions_total = Counter(
    "demobank_eligibility_decisions_total",
    "Deterministic eligibility outcomes",
    ["outcome", "product", "policy_version"],
)

rule_evaluations_total = Counter(
    "demobank_rule_evaluations_total",
    "Individual criterion evaluations",
    ["rule_id", "status", "severity", "policy_version"],
)

rule_failures_total = Counter(
    "demobank_rule_failures_total",
    "Criteria that failed, by criterion and policy version",
    ["rule_id", "policy_version", "severity"],
)

rule_engine_duration_seconds = Histogram(
    "demobank_rule_engine_duration_seconds",
    "Time spent in the deterministic rule engine",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

policy_version_applied_total = Counter(
    "demobank_policy_version_applied_total",
    "How often each policy version was applied",
    ["policy_version", "resolution"],
)

# --------------------------------------------------------------------------
# RAG
# --------------------------------------------------------------------------
rag_retrieval_duration_seconds = Histogram(
    "demobank_rag_retrieval_duration_seconds",
    "Policy retrieval latency",
    ["policy_version"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

rag_chunks_returned = Histogram(
    "demobank_rag_chunks_returned",
    "Number of policy chunks returned per request",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20),
)

rag_empty_retrievals_total = Counter(
    "demobank_rag_empty_retrievals_total",
    "Retrievals that returned no policy chunk for a cited clause",
    ["policy_version"],
)

citations_emitted_total = Counter(
    "demobank_citations_emitted_total",
    "Citations returned to clients",
    ["policy_version"],
)

# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
llm_stream_duration_seconds = Histogram(
    "demobank_llm_stream_duration_seconds",
    "Wall-clock time to stream one explanation",
    ["mode"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 60.0),
)

llm_tokens_total = Counter(
    "demobank_llm_tokens_total",
    "Tokens reported by the Anthropic API",
    ["direction", "model"],
)

llm_errors_total = Counter(
    "demobank_llm_errors_total", "Errors raised while streaming an explanation"
)

llm_fallbacks_total = Counter(
    "demobank_llm_fallbacks_total",
    "Explanations served by the deterministic template instead of Claude",
    ["reason"],
)

active_explanation_streams = Gauge(
    "demobank_active_explanation_streams", "Explanation streams currently open"
)

# --------------------------------------------------------------------------
# Guardrails, extraction, audit
# --------------------------------------------------------------------------
guardrail_violations_total = Counter(
    "demobank_guardrail_violations_total",
    "Guardrail violations detected in generated explanations",
    ["violation", "outcome"],
)

guardrail_checks_total = Counter(
    "demobank_guardrail_checks_total", "Explanations checked by the guardrails", ["result"]
)

extraction_documents_total = Counter(
    "demobank_extraction_documents_total", "Applicant documents parsed", ["parser", "result"]
)

extraction_fields_total = Counter(
    "demobank_extraction_fields_total",
    "Applicant fields extracted or missing",
    ["field", "result"],
)

audit_records_total = Counter(
    "demobank_audit_records_total", "Audit records written", ["outcome"]
)

audit_write_errors_total = Counter(
    "demobank_audit_write_errors_total", "Failures while writing an audit record"
)

VIOLATION_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalise_violation(violation: str) -> str:
    """Collapse a free-text violation into a low-cardinality label."""
    slug = VIOLATION_SLUG_RE.sub("_", violation.lower()).strip("_")
    parts = [part for part in slug.split("_") if part][:4]
    return "_".join(parts) or "unknown"


def set_build_info(
    llm_mode: str,
    embedding_backend: str,
    vector_backend: str,
    default_policy_version: str,
    policy_versions: Iterable[str],
    app_env: str,
) -> None:
    app_info.info(
        {
            "version": __version__,
            "engine_version": ENGINE_VERSION,
            "llm_mode": llm_mode,
            "embedding_backend": embedding_backend,
            "vector_backend": vector_backend,
            "default_policy_version": default_policy_version,
            "policy_versions": ",".join(policy_versions),
            "app_env": app_env,
            "data": "synthetic-demobank-only",
        }
    )


def observe_decision(decision, resolution: str = "explicit") -> None:
    """Record every metric derived from one deterministic decision."""
    eligibility_decisions_total.labels(
        outcome=decision.outcome.value,
        product=decision.product.value,
        policy_version=decision.policy_version,
    ).inc()
    policy_version_applied_total.labels(
        policy_version=decision.policy_version, resolution=resolution
    ).inc()
    for result in decision.rule_results:
        rule_evaluations_total.labels(
            rule_id=result.rule_id,
            status=result.status.value,
            severity=result.severity.value,
            policy_version=decision.policy_version,
        ).inc()
        if result.status.value == "FAIL":
            rule_failures_total.labels(
                rule_id=result.rule_id,
                policy_version=decision.policy_version,
                severity=result.severity.value,
            ).inc()


def observe_guardrails(violations: Iterable[str], outcome: str) -> None:
    violations = list(violations)
    guardrail_checks_total.labels(result="fail" if violations else "pass").inc()
    for violation in violations:
        guardrail_violations_total.labels(
            violation=normalise_violation(violation), outcome=outcome
        ).inc()


def observe_llm(mode: str, latency_seconds: float, model: Optional[str], outcome_record) -> None:
    llm_stream_duration_seconds.labels(mode=mode).observe(max(latency_seconds, 0.0))
    label_model = model or "none"
    if getattr(outcome_record, "input_tokens", None):
        llm_tokens_total.labels(direction="input", model=label_model).inc(
            outcome_record.input_tokens
        )
    if getattr(outcome_record, "output_tokens", None):
        llm_tokens_total.labels(direction="output", model=label_model).inc(
            outcome_record.output_tokens
        )
    if getattr(outcome_record, "error", None):
        llm_errors_total.inc()
    if getattr(outcome_record, "fell_back", False):
        llm_fallbacks_total.labels(reason="api_error").inc()
