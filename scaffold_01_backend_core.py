#!/usr/bin/env python3
"""Scaffolds backend/ core: requirements, Dockerfile, config, models, metrics,
streaming helper, and the FastAPI app entrypoint."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

FILES["backend/requirements.txt"] = r'''fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.2
pydantic-settings==2.5.2
sentence-transformers==3.1.1
rank-bm25==0.2.2
numpy==1.26.4
pyyaml==6.0.2
prometheus-client==0.20.0
anthropic==0.34.2
httpx==0.27.2
pytest==8.3.3
'''

FILES["backend/Dockerfile"] = r'''FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Best-effort: pre-build the hybrid retrieval index at image build time so the
# container starts fast. If this fails (e.g. no network during build), the
# app will build the index lazily on startup instead - see app/main.py.
RUN python scripts/build_index.py || true

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''

FILES["backend/pytest.ini"] = r'''[pytest]
pythonpath = .
'''

FILES["backend/app/__init__.py"] = r'''
'''

FILES["backend/app/config.py"] = r'''from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # .../backend


class Settings(BaseSettings):
    """Central configuration. Values are read from environment variables first
    (this is how docker-compose / CI inject them), falling back to backend/.env
    for local, non-Docker development, then to the defaults below.
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # "stub" runs fully offline with a deterministic templated answer - used
    # by default in tests/CI so the regression gate does not need API keys.
    # "anthropic" streams real answers from the Anthropic API.
    llm_provider: str = "stub"
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-5"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Which versioned rule set is currently "in force" for eligibility
    # decisions and for citing "the rule version used" in the audit log.
    current_rule_version: str = "v2"
    top_k: int = 5

    policy_dir: Path = BASE_DIR / "data" / "policies"
    rules_dir: Path = BASE_DIR / "data" / "eligibility_rules"
    index_dir: Path = BASE_DIR / "data" / "index"
    eval_set_path: Path = BASE_DIR / "data" / "eval" / "labelled_eligibility_set.jsonl"
    audit_log_path: Path = BASE_DIR / "data" / "audit" / "audit_log.jsonl"


settings = Settings()
settings.index_dir.mkdir(parents=True, exist_ok=True)
settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
'''

FILES["backend/app/models.py"] = r'''from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


class ApplicantProfile(BaseModel):
    monthly_income: float
    monthly_debt: float
    credit_score: int
    age: int
    employment_type: Literal["salaried", "self_employed", "unemployed"]
    requested_amount: float
    loan_tenure_months: int


class ChatRequest(BaseModel):
    session_id: str
    message: str
    applicant: Optional[ApplicantProfile] = None


class EligibilityDecision(BaseModel):
    outcome: Literal["eligible", "not_eligible", "needs_review"]
    reasons: list[str]
    rule_ids: list[str]
    rule_version: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[dict[str, Any]]
    decision: Optional[EligibilityDecision] = None
    rule_version: str
    latency_ms: float


class AuditRecord(BaseModel):
    timestamp: datetime
    session_id: str
    request_id: str
    question: str
    rule_version: str
    decision: Optional[EligibilityDecision] = None
    citations: list[dict[str, Any]]
    latency_ms: float
    error: Optional[str] = None
'''

FILES["backend/app/metrics.py"] = r'''from prometheus_client import Counter, Histogram

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
'''

FILES["backend/app/streaming.py"] = r'''import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Optional

from .llm.client import LLMClient
from .metrics import STREAM_ERRORS_TOTAL, TOKENS_STREAMED_TOTAL

logger = logging.getLogger("loan_assistant.streaming")


async def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_chat_response(
    llm: LLMClient,
    question: str,
    rule_version: str,
    decision: Optional[Any],
    chunks: list[dict],
    queue_maxsize: int = 32,
) -> AsyncGenerator[str, None]:
    """Bridges the LLM token producer and the HTTP consumer through a bounded
    asyncio.Queue. If the client reads slowly, `queue.put` blocks, which
    applies backpressure all the way back to token generation instead of the
    server buffering an unbounded amount of text in memory.

    Mid-stream provider failures (timeouts, API errors) are caught in the
    producer and surfaced to the client as a single SSE "error" event instead
    of crashing the connection or hanging it open.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
    sentinel = object()

    async def producer():
        try:
            async for token in llm.stream_answer(question, rule_version, decision, chunks):
                await queue.put(token)  # blocks if the consumer is slow
            await queue.put(sentinel)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any provider failure
            logger.exception("LLM streaming failed mid-stream")
            STREAM_ERRORS_TOTAL.inc()
            await queue.put(("__error__", str(exc)))
            await queue.put(sentinel)

    producer_task = asyncio.create_task(producer())
    full_answer: list[str] = []
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, tuple) and item and item[0] == "__error__":
                yield await sse_event("error", {"message": item[1]})
                break
            full_answer.append(item)
            TOKENS_STREAMED_TOTAL.inc()
            yield await sse_event("token", {"text": item})
    finally:
        if not producer_task.done():
            producer_task.cancel()

    yield await sse_event(
        "done",
        {
            "answer": "".join(full_answer),
            "rule_version": rule_version,
            "decision": decision.model_dump() if decision else None,
            "citations": chunks,
        },
    )
'''

FILES["backend/app/main.py"] = r'''import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from prometheus_client import make_asgi_app

from .audit.logger import AuditLogger
from .config import settings
from .eligibility.rules_engine import RulesEngine
from .llm.client import LLMClient
from .metrics import (
    ELIGIBILITY_DECISIONS_TOTAL,
    REQUESTS_TOTAL,
    REQUEST_LATENCY,
    RETRIEVAL_LATENCY,
)
from .models import AuditRecord, ChatRequest, ChatResponse
from .rag.hybrid_retriever import HybridRetriever
from .streaming import stream_chat_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loan_assistant.main")

app = FastAPI(title="Loan Eligibility Assistant", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/metrics", make_asgi_app())

retriever = HybridRetriever(settings.embedding_model, settings.index_dir)
rules_engine = RulesEngine(settings.rules_dir)
llm_client = LLMClient(settings.llm_provider, settings.anthropic_model, settings.anthropic_api_key)
audit_logger = AuditLogger(settings.audit_log_path)


@app.on_event("startup")
async def startup() -> None:
    if not retriever.load():
        logger.info("No persisted index found, building from %s", settings.policy_dir)
        retriever.build(settings.policy_dir)
    logger.info("Rule versions available: %s", rules_engine.available_versions())


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "rule_version": settings.current_rule_version,
        "available_rule_versions": rules_engine.available_versions(),
        "llm_provider": settings.llm_provider,
    }


@app.get("/v1/policy/version")
async def policy_version():
    return {
        "current_rule_version": settings.current_rule_version,
        "available_rule_versions": rules_engine.available_versions(),
    }


def _prepare(chat_request: ChatRequest):
    t0 = time.perf_counter()
    chunks = retriever.search(chat_request.message, top_k=settings.top_k)
    RETRIEVAL_LATENCY.observe(time.perf_counter() - t0)

    decision = None
    if chat_request.applicant is not None:
        decision = rules_engine.evaluate(chat_request.applicant, settings.current_rule_version)
        ELIGIBILITY_DECISIONS_TOTAL.labels(
            outcome=decision.outcome, rule_version=decision.rule_version
        ).inc()
    return chunks, decision


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(chat_request: ChatRequest):
    """Non-streaming endpoint. Used by promptfoo / the regression-eval script
    and by any simple client that does not need token-by-token streaming."""
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    chunks, decision = _prepare(chat_request)

    answer_parts = []
    async for token in llm_client.stream_answer(
        chat_request.message, settings.current_rule_version, decision, chunks
    ):
        answer_parts.append(token)
    answer = "".join(answer_parts)
    latency_ms = (time.perf_counter() - t0) * 1000

    REQUEST_LATENCY.labels(endpoint="/v1/chat").observe(latency_ms / 1000)
    REQUESTS_TOTAL.labels(endpoint="/v1/chat", status="ok").inc()

    audit_logger.log(
        AuditRecord(
            timestamp=datetime.now(timezone.utc),
            session_id=chat_request.session_id,
            request_id=request_id,
            question=chat_request.message,
            rule_version=settings.current_rule_version,
            decision=decision,
            citations=chunks,
            latency_ms=latency_ms,
        )
    )

    return ChatResponse(
        session_id=chat_request.session_id,
        answer=answer,
        citations=chunks,
        decision=decision,
        rule_version=settings.current_rule_version,
        latency_ms=latency_ms,
    )


@app.post("/v1/chat/stream")
async def chat_stream(chat_request: ChatRequest, request: Request):
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    chunks, decision = _prepare(chat_request)

    async def event_source():
        error_seen = False
        async for event in stream_chat_response(
            llm_client, chat_request.message, settings.current_rule_version, decision, chunks
        ):
            if await request.is_disconnected():
                logger.warning("Client disconnected mid-stream for request %s", request_id)
                break
            if event.startswith("event: error"):
                error_seen = True
            yield event

        latency_ms = (time.perf_counter() - t0) * 1000
        REQUEST_LATENCY.labels(endpoint="/v1/chat/stream").observe(latency_ms / 1000)
        REQUESTS_TOTAL.labels(
            endpoint="/v1/chat/stream", status="error" if error_seen else "ok"
        ).inc()
        audit_logger.log(
            AuditRecord(
                timestamp=datetime.now(timezone.utc),
                session_id=chat_request.session_id,
                request_id=request_id,
                question=chat_request.message,
                rule_version=settings.current_rule_version,
                decision=decision,
                citations=chunks,
                latency_ms=latency_ms,
                error="stream_error" if error_seen else None,
            )
        )

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/v1/audit/recent")
async def recent_audit(n: int = 20):
    return audit_logger.tail(n)
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
