"""Audit writing and querying."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select

from app.audit.db import get_session_factory, init_db
from app.audit.models import AuditRecord
from app.schemas import (
    ApplicantProfile,
    AuditRecordOut,
    Citation,
    EligibilityDecision,
    ExplanationMode,
)

logger = logging.getLogger(__name__)

# Fields excluded from the fingerprint so the audit trail never depends on a name.
FINGERPRINT_EXCLUDE = {"full_name", "notes", "applicant_reference"}


def fingerprint_applicant(applicant: ApplicantProfile) -> str:
    payload = {
        key: value
        for key, value in applicant.model_dump(mode="json").items()
        if key not in FINGERPRINT_EXCLUDE
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def serialise_rules(decision: EligibilityDecision) -> List[Dict[str, Any]]:
    return [
        {
            "rule_id": result.rule_id,
            "title": result.title,
            "category": result.category,
            "severity": result.severity.value,
            "status": result.status.value,
            "detail": result.detail,
            "threshold": result.threshold,
            "observed": result.observed,
            "missing_fields": result.missing_fields,
            "document_id": result.policy_reference.document_id,
            "section": result.policy_reference.section,
            "policy_version": result.policy_reference.policy_version,
        }
        for result in decision.rule_results
    ]


def serialise_citations(citations: Sequence[Citation]) -> List[Dict[str, Any]]:
    return [citation.model_dump(mode="json") for citation in citations]


class AuditLogger:
    """Writes one immutable row per pre-qualification outcome."""

    def __init__(self) -> None:
        init_db()
        self._sessions = get_session_factory()

    def record(
        self,
        *,
        trace_id: str,
        applicant: ApplicantProfile,
        decision: EligibilityDecision,
        citations: Sequence[Citation] = (),
        retrieved_chunks: int = 0,
        retrieval_latency_ms: float = 0.0,
        explanation: Optional[str] = None,
        explanation_mode: ExplanationMode = ExplanationMode.DETERMINISTIC_TEMPLATE,
        llm_model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[float] = None,
        guardrail_violations: Optional[List[str]] = None,
        question: Optional[str] = None,
        llm_error: Optional[str] = None,
    ) -> int:
        row = AuditRecord(
            trace_id=trace_id,
            applicant_reference=applicant.applicant_reference or "DEMO-APPLICANT",
            applicant_fingerprint=fingerprint_applicant(applicant),
            product=decision.product.value,
            policy_version=decision.policy_version,
            outcome=decision.outcome.value,
            decision_hash=decision.decision_hash,
            engine_version=decision.engine_version,
            rule_results=serialise_rules(decision),
            computed_facts=decision.computed_facts,
            required_actions=decision.required_actions,
            citations=serialise_citations(citations),
            retrieved_chunks=retrieved_chunks,
            retrieval_latency_ms=round(retrieval_latency_ms, 3),
            explanation_mode=getattr(explanation_mode, "value", str(explanation_mode)),
            llm_model=llm_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 3) if latency_ms is not None else None,
            explanation=explanation,
            question=question,
            guardrail_violations=list(guardrail_violations or []),
            llm_error=llm_error,
        )
        with self._sessions() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            logger.info(
                "audit id=%s trace=%s outcome=%s policy=%s hash=%s",
                row.id,
                trace_id,
                row.outcome,
                row.policy_version,
                row.decision_hash[:12],
            )
            return int(row.id)

    # ------------------------------------------------------------------
    def get(self, record_id: int) -> Optional[AuditRecordOut]:
        with self._sessions() as session:
            row = session.get(AuditRecord, record_id)
            return AuditRecordOut(**row.as_dict()) if row else None

    def list_recent(
        self,
        limit: int = 25,
        offset: int = 0,
        outcome: Optional[str] = None,
        policy_version: Optional[str] = None,
        product: Optional[str] = None,
    ) -> List[AuditRecordOut]:
        statement = select(AuditRecord).order_by(AuditRecord.id.desc())
        if outcome:
            statement = statement.where(AuditRecord.outcome == outcome)
        if policy_version:
            statement = statement.where(AuditRecord.policy_version == policy_version)
        if product:
            statement = statement.where(AuditRecord.product == product)
        statement = statement.limit(max(1, min(limit, 200))).offset(max(0, offset))
        with self._sessions() as session:
            rows = session.execute(statement).scalars().all()
            return [AuditRecordOut(**row.as_dict()) for row in rows]

    def count(self) -> int:
        with self._sessions() as session:
            return int(session.execute(select(func.count(AuditRecord.id))).scalar() or 0)

    def outcome_breakdown(self) -> Dict[str, int]:
        statement = select(AuditRecord.outcome, func.count(AuditRecord.id)).group_by(
            AuditRecord.outcome
        )
        with self._sessions() as session:
            return {str(outcome): int(count) for outcome, count in session.execute(statement)}


_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def reset_audit_logger() -> None:
    global _audit_logger
    _audit_logger = None
