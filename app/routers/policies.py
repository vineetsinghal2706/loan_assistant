"""Policy version introspection and policy search (the RAG surface)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.rag.ingest import PolicyIngestor
from app.rag.retriever import get_retriever
from app.rules.registry import PolicyVersionNotFound, ProductNotSupported, get_registry
from app.schemas import PolicySearchResult, PolicyVersionInfo

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("/versions", response_model=List[PolicyVersionInfo], summary="List policy versions")
def list_versions() -> List[PolicyVersionInfo]:
    return get_registry().all_info()


@router.get(
    "/versions/{policy_version}",
    response_model=PolicyVersionInfo,
    summary="Describe one policy version",
)
def get_version(policy_version: str) -> PolicyVersionInfo:
    try:
        return get_registry().info(policy_version)
    except PolicyVersionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/versions/{policy_version}/rules", summary="List the encoded criteria")
def list_rules(policy_version: str, product: Optional[str] = None) -> dict:
    registry = get_registry()
    try:
        pack = registry.get(policy_version)
    except PolicyVersionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    product_names = [product] if product else sorted(pack.products.keys())
    payload = {}
    for name in product_names:
        try:
            config = pack.product(name)
        except ProductNotSupported as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload[name] = {
            "display_name": config.display_name,
            "assumptions": dict(config.assumptions),
            "limits": dict(config.limits),
            "rules": [
                {
                    "id": rule.id,
                    "title": rule.title,
                    "category": rule.category,
                    "severity": rule.severity.value,
                    "threshold": rule.threshold,
                    "condition": rule.condition,
                    "applies_when": rule.applies_when,
                    "policy_reference": dict(rule.policy_reference),
                }
                for rule in config.rules
            ],
        }
    return {
        "policy_version": pack.policy_version,
        "status": pack.status,
        "effective_from": str(pack.effective_from),
        "effective_to": str(pack.effective_to) if pack.effective_to else None,
        "change_log": pack.change_log,
        "products": payload,
    }


@router.get("/search", response_model=PolicySearchResult, summary="Semantic policy search")
def search(
    q: str = Query(..., min_length=2, description="Natural-language policy question"),
    policy_version: Optional[str] = Query(
        default=None, description="Restrict to one policy version"
    ),
    top_k: int = Query(default=5, ge=1, le=20),
) -> PolicySearchResult:
    registry = get_registry()
    version = policy_version or registry.resolve().policy_version
    try:
        registry.get(version)
    except PolicyVersionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    chunks = get_retriever().search(q, policy_version=version, top_k=top_k)
    return PolicySearchResult(query=q, policy_version=version, chunks=chunks)


@router.post("/reindex", summary="Re-ingest the synthetic policy corpus")
def reindex(rebuild: bool = Query(default=False)) -> dict:
    stats = PolicyIngestor().ingest(rebuild=rebuild)
    return {"status": "ok", **stats.as_dict()}
