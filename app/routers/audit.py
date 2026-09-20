"""Audit log access.

Every row is reproducible: the same applicant facts, product and policy version
must produce the same ``decision_hash``.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.audit.logger import get_audit_logger
from app.schemas import AuditRecordOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get(
    "/decisions",
    response_model=List[AuditRecordOut],
    summary="Recent pre-qualification outcomes",
)
def list_decisions(
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    outcome: Optional[str] = None,
    policy_version: Optional[str] = None,
    product: Optional[str] = None,
) -> List[AuditRecordOut]:
    return get_audit_logger().list_recent(
        limit=limit,
        offset=offset,
        outcome=outcome,
        policy_version=policy_version,
        product=product,
    )


@router.get(
    "/decisions/{record_id}",
    response_model=AuditRecordOut,
    summary="One audit record, including criteria and citations",
)
def get_decision(record_id: int) -> AuditRecordOut:
    record = get_audit_logger().get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No audit record with id {record_id}")
    return record


@router.get("/summary", summary="Outcome counts across the audit log")
def summary() -> dict:
    logger = get_audit_logger()
    return {
        "total": logger.count(),
        "by_outcome": logger.outcome_breakdown(),
        "notice": "Synthetic educational data only.",
    }
