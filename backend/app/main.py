import logging
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
