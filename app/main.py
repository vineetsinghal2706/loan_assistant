"""FastAPI application factory.

    Streamlit UI -> FastAPI -> {rule engine, RAG, Claude} -> SQLite audit
                                     |
                                     +-> /metrics -> Prometheus -> Grafana
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app import ENGINE_VERSION, __version__
from app.audit.db import init_db
from app.config import get_settings
from app.llm.claude_client import get_explainer
from app.observability import ObservabilityMiddleware, metrics
from app.rag.embeddings import get_embedder
from app.rag.ingest import PolicyIngestor
from app.rag.retriever import get_retriever
from app.routers import audit, chat, documents, eligibility, health, policies
from app.rules.registry import get_registry

logger = logging.getLogger(__name__)

SYNTHETIC_NOTICE = "DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY"

DESCRIPTION = f"""
Streaming conversational **Loan Eligibility Assistant** for the fictional **DemoBank**.

{SYNTHETIC_NOTICE}

The assistant never asks the model "is this customer eligible?". Instead:

1. **Extraction** turns applicant documents into structured facts with provenance.
2. **RAG** answers *what does the policy say?* for one specific policy version.
3. A **deterministic rule engine** answers *does this applicant satisfy those rules?*
   and is the sole source of truth for the outcome.
4. **Claude** answers only *how should this result be explained?*, with citations.
5. Guardrails, an **audit log** and **Prometheus** metrics record what happened.

No real customer data, no real bank policy, no real credit decisions.
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_dirs()

    init_db()
    registry = get_registry()
    embedder = get_embedder()
    retriever = get_retriever()

    try:
        stats = PolicyIngestor(embedder=embedder, store=retriever.store).ensure_index()
        logger.info("Policy index ready: %s", stats.as_dict())
    except Exception:  # pragma: no cover - keep the API up even if indexing fails
        logger.exception("Policy ingestion failed at startup; retrieval will be degraded")

    for version in registry.versions:
        metrics.policy_index_chunks.labels(policy_version=version).set(
            retriever.count(policy_version=version)
        )

    explainer = get_explainer()
    metrics.set_build_info(
        llm_mode=explainer.mode.value,
        embedding_backend=embedder.name,
        vector_backend=retriever.backend,
        default_policy_version=settings.default_policy_version,
        policy_versions=registry.versions,
        app_env=settings.app_env,
    )
    logger.info(
        "Loan Eligibility Assistant ready (llm=%s, embedder=%s, store=%s, policies=%s)",
        explainer.mode.value,
        embedder.name,
        retriever.backend,
        ", ".join(registry.versions),
    )
    yield
    logger.info("Loan Eligibility Assistant shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        contact={"name": "AI Services Capstone (educational demo)"},
        license_info={"name": "Educational use only"},
    )

    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # demo only; restrict in any real deployment
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    app.include_router(health.router)
    app.include_router(policies.router)
    app.include_router(documents.router)
    app.include_router(eligibility.router)
    app.include_router(chat.router)
    app.include_router(audit.router)

    # Prometheus scrape target
    app.mount("/metrics", make_asgi_app())

    @app.get("/", tags=["health"], summary="Service index")
    def index() -> dict:
        return {
            "service": settings.app_name,
            "version": __version__,
            "engine_version": ENGINE_VERSION,
            "notice": SYNTHETIC_NOTICE,
            "pipeline": [
                "applicant data",
                "document extraction",
                "RAG (policy version scoped)",
                "deterministic rule engine",
                "Claude explanation",
                "streaming response",
                "citations + audit log",
                "Prometheus metrics",
            ],
            "endpoints": {
                "docs": "/docs",
                "health": "/health",
                "policy_versions": "/policies/versions",
                "policy_search": "/policies/search?q=credit+score",
                "extract": "/documents/extract",
                "evaluate": "/eligibility/evaluate",
                "explain": "/eligibility/explain",
                "stream": "/eligibility/stream",
                "compare_versions": "/eligibility/compare-versions",
                "chat_stream": "/chat/stream",
                "audit": "/audit/decisions",
                "metrics": "/metrics",
            },
        }

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "tr_unknown")
        logger.exception("Unhandled exception (trace %s)", trace_id)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal error in the educational demo.",
                "trace_id": trace_id,
                "error": str(exc),
            },
        )

    return app


app = create_app()
