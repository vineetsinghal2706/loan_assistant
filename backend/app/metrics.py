from prometheus_client import Counter, Histogram

REQUEST_LATENCY = Histogram(
    "loan_assistant_request_latency_seconds",
    "End-to-end latency of chat requests",
    ["endpoint"],
)

RETRIEVAL_LATENCY = Histogram(
    "loan_assistant_retrieval_latency_seconds",
    "Hybrid (semantic + lexical) retrieval latency",
)

REQUESTS_TOTAL = Counter(
    "loan_assistant_requests_total",
    "Total chat requests",
    ["endpoint", "status"],
)

ELIGIBILITY_DECISIONS_TOTAL = Counter(
    "loan_assistant_eligibility_decisions_total",
    "Eligibility decisions by outcome and rule version",
    ["outcome", "rule_version"],
)

STREAM_ERRORS_TOTAL = Counter(
    "loan_assistant_stream_errors_total",
    "Mid-stream errors encountered while streaming an answer",
)

TOKENS_STREAMED_TOTAL = Counter(
    "loan_assistant_tokens_streamed_total",
    "Total tokens/words streamed to clients",
)
