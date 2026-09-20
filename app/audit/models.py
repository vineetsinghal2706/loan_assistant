"""SQLAlchemy models for the audit log.

Section DOC-5.1 of the synthetic documentation standard requires that every
outcome be reproducible: policy version, criterion-by-criterion results, the
values relied upon and the clauses cited are all stored. Section DOC-4.1
requires data minimisation, so the applicant is stored as a pseudonymous
reference plus a salted-free fingerprint of the *facts* - never a name, an
identity number or a raw document.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditRecord(Base):
    __tablename__ = "audit_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    # Applicant (pseudonymous only)
    applicant_reference: Mapped[str] = mapped_column(String(64), index=True)
    applicant_fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    # Decision
    product: Mapped[str] = mapped_column(String(32), index=True)
    policy_version: Mapped[str] = mapped_column(String(16), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    decision_hash: Mapped[str] = mapped_column(String(64), index=True)
    engine_version: Mapped[str] = mapped_column(String(32))
    rule_results: Mapped[dict] = mapped_column(JSON, default=list)
    computed_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    required_actions: Mapped[dict] = mapped_column(JSON, default=list)

    # Retrieval
    citations: Mapped[dict] = mapped_column(JSON, default=list)
    retrieved_chunks: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    # Explanation
    explanation_mode: Mapped[str] = mapped_column(String(32), default="deterministic_template")
    llm_model: Mapped[str] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=True)
    guardrail_violations: Mapped[dict] = mapped_column(JSON, default=list)
    llm_error: Mapped[str] = mapped_column(Text, nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "applicant_reference": self.applicant_reference,
            "applicant_fingerprint": self.applicant_fingerprint,
            "product": self.product,
            "policy_version": self.policy_version,
            "outcome": self.outcome,
            "decision_hash": self.decision_hash,
            "engine_version": self.engine_version,
            "explanation_mode": self.explanation_mode,
            "llm_model": self.llm_model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "guardrail_violations": self.guardrail_violations or [],
            "rule_results": self.rule_results or [],
            "citations": self.citations or [],
            "computed_facts": self.computed_facts or {},
            "explanation": self.explanation,
        }
