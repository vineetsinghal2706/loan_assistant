"""Conversational endpoint.

The conversation is *not* the decision maker. Every turn re-runs the
deterministic engine on the structured applicant profile and streams the
outcome before any generated text, so conversation history can never talk the
assistant into a different answer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.pipeline import get_pipeline
from app.routers.eligibility import SSE_HEADERS, ExplainedEligibilityResponse
from app.rules.registry import PolicyVersionNotFound, ProductNotSupported
from app.schemas import ChatRequest

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/stream",
    summary="Streaming conversational pre-qualification (server-sent events)",
    response_class=StreamingResponse,
)
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    pipeline = get_pipeline()
    generator = pipeline.stream_events(
        applicant=payload.applicant,
        product=payload.product,
        trace_id=getattr(request.state, "trace_id", "tr_unknown"),
        policy_version=payload.policy_version,
        as_of=payload.as_of_date,
        question=payload.question,
        history=[turn.model_dump() for turn in payload.history],
        stream_tokens=payload.stream_tokens,
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post(
    "",
    response_model=ExplainedEligibilityResponse,
    summary="Non-streaming conversational pre-qualification",
)
async def chat(request: Request, payload: ChatRequest) -> ExplainedEligibilityResponse:
    pipeline = get_pipeline()
    try:
        result = await pipeline.run(
            applicant=payload.applicant,
            product=payload.product,
            trace_id=getattr(request.state, "trace_id", "tr_unknown"),
            policy_version=payload.policy_version,
            as_of=payload.as_of_date,
            question=payload.question,
            explain=True,
        )
    except PolicyVersionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductNotSupported as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    base = pipeline.to_response(result)
    return ExplainedEligibilityResponse(
        **base.model_dump(),
        explanation=result.explanation,
        explanation_mode=result.explanation_mode,
        guardrail_violations=result.guardrail_violations,
    )
