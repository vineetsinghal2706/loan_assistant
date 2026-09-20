"""Eligibility endpoints.

``/eligibility/evaluate`` is the pure deterministic answer with citations and no
LLM involvement at all - useful for regression testing and for showing that the
decision does not depend on the model. ``/eligibility/stream`` adds the
streamed Claude explanation.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.pipeline import get_pipeline
from app.rules.engine import RuleEngine
from app.rules.registry import PolicyVersionNotFound, ProductNotSupported, get_registry
from app.schemas import (
    EligibilityRequest,
    EligibilityResponse,
    ExplanationMode,
    VersionComparisonResponse,
)

router = APIRouter(prefix="/eligibility", tags=["eligibility"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ExplainedEligibilityResponse(EligibilityResponse):
    explanation: str = ""
    explanation_mode: ExplanationMode = ExplanationMode.DETERMINISTIC_TEMPLATE
    guardrail_violations: List[str] = Field(default_factory=list)


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "tr_unknown")


def _guard(exc: Exception) -> HTTPException:
    if isinstance(exc, PolicyVersionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProductNotSupported):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/evaluate",
    response_model=EligibilityResponse,
    summary="Deterministic eligibility decision with citations (no LLM)",
)
async def evaluate(request: Request, payload: EligibilityRequest) -> EligibilityResponse:
    pipeline = get_pipeline()
    try:
        result = await pipeline.run(
            applicant=payload.applicant,
            product=payload.product,
            trace_id=_trace_id(request),
            policy_version=payload.policy_version,
            as_of=payload.as_of_date,
            question=payload.question,
            explain=False,
        )
    except (PolicyVersionNotFound, ProductNotSupported) as exc:
        raise _guard(exc) from exc
    return pipeline.to_response(result)


@router.post(
    "/explain",
    response_model=ExplainedEligibilityResponse,
    summary="Deterministic decision plus a complete (non-streamed) explanation",
)
async def explain(request: Request, payload: EligibilityRequest) -> ExplainedEligibilityResponse:
    pipeline = get_pipeline()
    try:
        result = await pipeline.run(
            applicant=payload.applicant,
            product=payload.product,
            trace_id=_trace_id(request),
            policy_version=payload.policy_version,
            as_of=payload.as_of_date,
            question=payload.question,
            explain=True,
        )
    except (PolicyVersionNotFound, ProductNotSupported) as exc:
        raise _guard(exc) from exc

    base = pipeline.to_response(result)
    return ExplainedEligibilityResponse(
        **base.model_dump(),
        explanation=result.explanation,
        explanation_mode=result.explanation_mode,
        guardrail_violations=result.guardrail_violations,
    )


@router.post(
    "/stream",
    summary="Server-sent events: decision first, then the streamed explanation",
    response_class=StreamingResponse,
)
async def stream(request: Request, payload: EligibilityRequest) -> StreamingResponse:
    pipeline = get_pipeline()
    generator = pipeline.stream_events(
        applicant=payload.applicant,
        product=payload.product,
        trace_id=_trace_id(request),
        policy_version=payload.policy_version,
        as_of=payload.as_of_date,
        question=payload.question,
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post(
    "/compare-versions",
    response_model=VersionComparisonResponse,
    summary="Run the same applicant against several policy versions",
)
def compare_versions(payload: EligibilityRequest = Body(...)) -> VersionComparisonResponse:
    registry = get_registry()
    engine = RuleEngine(registry)
    versions = registry.versions
    try:
        return engine.compare_versions(
            payload.applicant, product=payload.product, versions=versions
        )
    except (PolicyVersionNotFound, ProductNotSupported) as exc:
        raise _guard(exc) from exc
