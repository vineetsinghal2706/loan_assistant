"""The orchestration layer: applicant -> rules -> RAG -> Claude -> audit.

The order of operations is deliberate and enforced here rather than in the
routers:

1. The deterministic rule engine produces the outcome.
2. RAG retrieves the clauses *of the policy version that was applied*.
3. Claude explains the outcome using only those clauses.
4. Guardrails verify the explanation did not contradict the outcome.
5. The audit record is written and metrics are updated.

Because step 1 happens before step 3, the client receives the authoritative
decision before the first generated token.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from app.audit.logger import AuditLogger, get_audit_logger
from app.llm.claude_client import ClaudeExplainer, StreamOutcome, get_explainer
from app.llm.guardrails import validate_explanation
from app.llm.prompts import render_deterministic_explanation
from app.observability import metrics
from app.rag.retriever import PolicyRetriever, get_retriever
from app.rules.engine import RuleEngine, summarise_decision
from app.schemas import (
    ApplicantProfile,
    Citation,
    EligibilityDecision,
    EligibilityResponse,
    ExplanationMode,
    LoanProduct,
    RuleStatus,
)

logger = logging.getLogger(__name__)


def sse(event: str, data: Any) -> str:
    """Format one Server-Sent Event frame."""
    if isinstance(data, str):
        payload = json.dumps({"text": data})
    else:
        payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@dataclass
class RetrievalBundle:
    citations: List[Citation] = field(default_factory=list)
    chunks: int = 0
    latency_ms: float = 0.0


@dataclass
class PipelineResult:
    trace_id: str
    decision: EligibilityDecision
    citations: List[Citation]
    retrieved_chunks: int
    explanation: str
    explanation_mode: ExplanationMode
    guardrail_violations: List[str]
    audit_id: Optional[int] = None


class EligibilityPipeline:
    def __init__(
        self,
        engine: Optional[RuleEngine] = None,
        retriever: Optional[PolicyRetriever] = None,
        explainer: Optional[ClaudeExplainer] = None,
        audit: Optional[AuditLogger] = None,
    ) -> None:
        self.engine = engine or RuleEngine()
        self.retriever = retriever or get_retriever()
        self.explainer = explainer or get_explainer()
        self.audit = audit or get_audit_logger()

    # ------------------------------------------------------------------
    # Step 1 - deterministic decision
    # ------------------------------------------------------------------
    def evaluate(
        self,
        applicant: ApplicantProfile,
        product: LoanProduct,
        policy_version: Optional[str] = None,
        as_of: Optional[date] = None,
    ) -> EligibilityDecision:
        resolution = (
            "explicit" if policy_version else ("as_of_date" if as_of else "default")
        )
        started = time.perf_counter()
        decision = self.engine.evaluate(
            applicant, product=product, policy_version=policy_version, as_of=as_of
        )
        metrics.rule_engine_duration_seconds.observe(time.perf_counter() - started)
        metrics.observe_decision(decision, resolution=resolution)
        return decision

    # ------------------------------------------------------------------
    # Step 2 - policy retrieval for citations
    # ------------------------------------------------------------------
    def retrieve(
        self, decision: EligibilityDecision, question: Optional[str] = None
    ) -> RetrievalBundle:
        started = time.perf_counter()
        try:
            citations, chunks = self.retriever.citations_for_decision(
                decision, question=question
            )
        except Exception as exc:  # pragma: no cover - store/backend failure
            logger.warning("Policy retrieval failed (%s); citing rule clauses directly.", exc)
            citations, chunks = self._fallback_citations(decision), 0
        elapsed = time.perf_counter() - started

        metrics.rag_retrieval_duration_seconds.labels(
            policy_version=decision.policy_version
        ).observe(elapsed)
        metrics.rag_chunks_returned.observe(chunks)
        if chunks == 0:
            metrics.rag_empty_retrievals_total.labels(
                policy_version=decision.policy_version
            ).inc()
        for _ in citations:
            metrics.citations_emitted_total.labels(
                policy_version=decision.policy_version
            ).inc()

        return RetrievalBundle(
            citations=citations, chunks=chunks, latency_ms=round(elapsed * 1000, 3)
        )

    @staticmethod
    def _fallback_citations(decision: EligibilityDecision) -> List[Citation]:
        """Cite the clause each rule declares, when the vector store is unavailable.

        Criteria that actually drove the decision are cited first, so the
        explanation can always attach a marker to the reason it gives.
        """
        driving = [
            result
            for result in decision.rule_results
            if result.status in {RuleStatus.FAIL, RuleStatus.UNKNOWN}
        ]
        driving_ids = {result.rule_id for result in driving}
        remainder = [
            result for result in decision.rule_results if result.rule_id not in driving_ids
        ]

        citations: List[Citation] = []
        seen = set()
        for result in driving + remainder:
            reference = result.policy_reference
            key = (reference.document_id, reference.section)
            if key in seen or not reference.quote:
                continue
            seen.add(key)
            citations.append(
                Citation(
                    marker=f"[{len(citations) + 1}]",
                    document_id=reference.document_id,
                    document_title=reference.document_title or reference.document_id,
                    section=reference.section,
                    policy_version=reference.policy_version,
                    quote=reference.quote,
                    rule_ids=[result.rule_id],
                )
            )
            if len(citations) >= 6:
                break
        return citations

    # ------------------------------------------------------------------
    # Steps 3-5 - explanation, guardrails, audit
    # ------------------------------------------------------------------
    async def stream_events(
        self,
        applicant: ApplicantProfile,
        product: LoanProduct,
        trace_id: str,
        policy_version: Optional[str] = None,
        as_of: Optional[date] = None,
        question: Optional[str] = None,
        history: Optional[Sequence[Dict[str, str]]] = None,
        stream_tokens: bool = True,
    ) -> AsyncIterator[str]:
        """Yield SSE frames for one conversational pre-qualification."""

        metrics.active_explanation_streams.inc()
        try:
            yield sse(
                "trace",
                {
                    "trace_id": trace_id,
                    "stage": "started",
                    "notice": "Synthetic DemoBank data - educational demonstration only.",
                },
            )

            decision = self.evaluate(applicant, product, policy_version, as_of)
            yield sse(
                "policy",
                {
                    "policy_version": decision.policy_version,
                    "effective_from": str(decision.policy_effective_from),
                    "effective_to": (
                        str(decision.policy_effective_to)
                        if decision.policy_effective_to
                        else None
                    ),
                    "engine_version": decision.engine_version,
                },
            )
            yield sse("decision", summarise_decision(decision))

            bundle = self.retrieve(decision, question=question)
            yield sse(
                "citations",
                {
                    "citations": [citation.model_dump(mode="json") for citation in bundle.citations],
                    "retrieved_chunks": bundle.chunks,
                    "retrieval_latency_ms": bundle.latency_ms,
                },
            )

            record = StreamOutcome()
            if stream_tokens:
                async for piece in self.explainer.stream(
                    decision,
                    bundle.citations,
                    question=question,
                    history=history,
                    outcome=record,
                ):
                    yield sse("token", {"text": piece})
            else:
                record = await self.explainer.explain(
                    decision, bundle.citations, question=question, history=history
                )
                yield sse("token", {"text": record.text})

            metrics.observe_llm(
                record.mode.value, record.latency_seconds, record.model, record
            )

            report = validate_explanation(record.text, decision, bundle.citations)
            metrics.observe_guardrails(report.violations, decision.outcome.value)
            explanation = record.text
            if not report.ok:
                correction = render_deterministic_explanation(decision, bundle.citations)
                explanation = correction
                yield sse(
                    "guardrail",
                    {
                        "ok": False,
                        "violations": report.violations,
                        "action": "explanation replaced with the deterministic summary",
                        "corrected_explanation": correction,
                    },
                )
            else:
                yield sse("guardrail", {"ok": True, "violations": [], "notes": report.notes})

            audit_id = self._write_audit(
                trace_id=trace_id,
                applicant=applicant,
                decision=decision,
                bundle=bundle,
                explanation=explanation,
                record=record,
                violations=report.violations,
                question=question,
            )
            yield sse(
                "audit",
                {
                    "audit_id": audit_id,
                    "decision_hash": decision.decision_hash,
                    "explanation_mode": record.mode.value,
                    "llm_model": record.model,
                },
            )
            yield sse("done", {"trace_id": trace_id, "outcome": decision.outcome.value})
        except Exception as exc:  # pragma: no cover - surfaced to the client
            logger.exception("Pipeline failure on trace %s", trace_id)
            yield sse("error", {"trace_id": trace_id, "message": str(exc)})
        finally:
            metrics.active_explanation_streams.dec()

    async def run(
        self,
        applicant: ApplicantProfile,
        product: LoanProduct,
        trace_id: str,
        policy_version: Optional[str] = None,
        as_of: Optional[date] = None,
        question: Optional[str] = None,
        explain: bool = True,
    ) -> PipelineResult:
        """Non-streaming variant used by the JSON endpoints and evaluations."""

        decision = self.evaluate(applicant, product, policy_version, as_of)
        bundle = self.retrieve(decision, question=question)

        if explain:
            record = await self.explainer.explain(
                decision, bundle.citations, question=question
            )
            metrics.observe_llm(
                record.mode.value, record.latency_seconds, record.model, record
            )
            report = validate_explanation(record.text, decision, bundle.citations)
            metrics.observe_guardrails(report.violations, decision.outcome.value)
            explanation = (
                record.text
                if report.ok
                else render_deterministic_explanation(decision, bundle.citations)
            )
            violations = report.violations
        else:
            record = StreamOutcome(mode=ExplanationMode.DETERMINISTIC_TEMPLATE)
            explanation = ""
            violations = []

        audit_id = self._write_audit(
            trace_id=trace_id,
            applicant=applicant,
            decision=decision,
            bundle=bundle,
            explanation=explanation or None,
            record=record,
            violations=violations,
            question=question,
        )

        return PipelineResult(
            trace_id=trace_id,
            decision=decision,
            citations=bundle.citations,
            retrieved_chunks=bundle.chunks,
            explanation=explanation,
            explanation_mode=record.mode,
            guardrail_violations=violations,
            audit_id=audit_id,
        )

    def to_response(self, result: PipelineResult) -> EligibilityResponse:
        return EligibilityResponse(
            decision=result.decision,
            citations=result.citations,
            retrieved_chunks=result.retrieved_chunks,
            audit_id=result.audit_id,
            trace_id=result.trace_id,
        )

    # ------------------------------------------------------------------
    def _write_audit(
        self,
        *,
        trace_id: str,
        applicant: ApplicantProfile,
        decision: EligibilityDecision,
        bundle: RetrievalBundle,
        explanation: Optional[str],
        record: StreamOutcome,
        violations: Sequence[str],
        question: Optional[str],
    ) -> Optional[int]:
        try:
            audit_id = self.audit.record(
                trace_id=trace_id,
                applicant=applicant,
                decision=decision,
                citations=bundle.citations,
                retrieved_chunks=bundle.chunks,
                retrieval_latency_ms=bundle.latency_ms,
                explanation=explanation,
                explanation_mode=record.mode,
                llm_model=record.model,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                latency_ms=round(record.latency_seconds * 1000, 3),
                guardrail_violations=list(violations),
                question=question,
                llm_error=record.error,
            )
            metrics.audit_records_total.labels(outcome=decision.outcome.value).inc()
            return audit_id
        except Exception:  # pragma: no cover - database failure must not break the answer
            logger.exception("Failed to write audit record for trace %s", trace_id)
            metrics.audit_write_errors_total.inc()
            return None


_pipeline: Optional[EligibilityPipeline] = None


def get_pipeline() -> EligibilityPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = EligibilityPipeline()
    return _pipeline


def reset_pipeline() -> None:
    global _pipeline
    _pipeline = None
