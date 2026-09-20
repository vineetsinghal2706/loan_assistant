"""Liveness, readiness and build information."""

from __future__ import annotations

from fastapi import APIRouter

from app import ENGINE_VERSION, __version__
from app.config import get_settings
from app.llm.claude_client import get_explainer
from app.rag.embeddings import get_embedder
from app.rag.retriever import get_retriever
from app.rules.registry import get_registry
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service and dependency status")
def health() -> HealthResponse:
    settings = get_settings()
    registry = get_registry()
    retriever = get_retriever()
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        app_env=settings.app_env,
        version=__version__,
        engine_version=ENGINE_VERSION,
        default_policy_version=settings.default_policy_version,
        policy_versions=registry.versions,
        llm_mode=get_explainer().mode,
        embedding_backend=get_embedder().name,
        vector_backend=retriever.backend,
        indexed_chunks=retriever.count(),
    )


@router.get("/health/live", summary="Liveness probe")
def live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready", summary="Readiness probe")
def ready() -> dict:
    registry = get_registry()
    retriever = get_retriever()
    chunks = retriever.count()
    ready_state = bool(registry.versions) and chunks > 0
    return {
        "status": "ready" if ready_state else "degraded",
        "policy_versions": registry.versions,
        "indexed_chunks": chunks,
        "detail": (
            "ok"
            if ready_state
            else "policy corpus is not indexed; run scripts/ingest_policies.py"
        ),
    }
