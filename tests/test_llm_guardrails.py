"""Claude is only allowed to explain. These tests police that boundary."""

from __future__ import annotations

import pytest

from app.llm.claude_client import ClaudeExplainer
from app.llm.guardrails import validate_explanation
from app.llm.prompts import (
    SYSTEM_PROMPT,
    build_explanation_prompt,
    render_deterministic_explanation,
)
from app.pipeline import EligibilityPipeline
from app.rules.engine import RuleEngine
from app.schemas import EligibilityOutcome, ExplanationMode


def _decision_and_citations(engine: RuleEngine, applicant, version: str = "2.0"):
    decision = engine.evaluate(applicant, policy_version=version)
    citations = EligibilityPipeline._fallback_citations(decision)
    return decision, citations


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
def test_system_prompt_forbids_deciding():
    lowered = SYSTEM_PROMPT.lower()
    assert "final" in lowered
    assert "never change it" in lowered
    assert "never assess eligibility yourself" in lowered
    assert "insufficient_information" in lowered
    assert "cite" in lowered


def test_prompt_contains_the_decision_and_the_clauses(engine: RuleEngine, clean_applicant):
    applicant = clean_applicant.model_copy(update={"credit_score": 600})
    decision, citations = _decision_and_citations(engine, applicant)
    prompt = build_explanation_prompt(decision, citations, question="Why not?")

    assert "<decision>" in prompt
    assert decision.outcome.value in prompt
    assert f"policy_version: {decision.policy_version}" in prompt
    assert "CR-2.1-CREDIT-SCORE" in prompt
    assert citations[0].marker in prompt
    assert "do not re-decide" in prompt.lower()


# ---------------------------------------------------------------------------
# Deterministic explanation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "update,expected_outcome",
    [
        ({}, EligibilityOutcome.ELIGIBLE),
        ({"documents_verified": False}, EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS),
        ({"credit_score": 600}, EligibilityOutcome.NOT_ELIGIBLE),
        ({"credit_score": None}, EligibilityOutcome.INSUFFICIENT_INFORMATION),
    ],
)
def test_template_explanation_passes_its_own_guardrails(
    engine: RuleEngine, clean_applicant, update, expected_outcome
):
    decision, citations = _decision_and_citations(
        engine, clean_applicant.model_copy(update=update)
    )
    assert decision.outcome is expected_outcome

    text = render_deterministic_explanation(decision, citations)
    report = validate_explanation(text, decision, citations)
    assert report.ok, report.violations
    assert decision.policy_version in text
    assert "not financial advice" in text


def test_insufficient_information_is_never_worded_as_a_decline(
    engine: RuleEngine, clean_applicant
):
    decision, citations = _decision_and_citations(
        engine, clean_applicant.model_copy(update={"credit_score": None})
    )
    text = render_deterministic_explanation(decision, citations).lower()
    assert "not a decline" in text
    for word in ("declined", "rejected", "refused"):
        assert word not in text


# ---------------------------------------------------------------------------
# Guardrail detection
# ---------------------------------------------------------------------------
def test_contradicting_a_positive_outcome_is_caught(engine: RuleEngine, clean_applicant):
    decision, citations = _decision_and_citations(engine, clean_applicant)
    report = validate_explanation(
        "Unfortunately you are not eligible for this product. [1]", decision, citations
    )
    assert not report.ok
    assert any("ELIGIBLE" in violation for violation in report.violations)


def test_contradicting_a_negative_outcome_is_caught(engine: RuleEngine, clean_applicant):
    decision, citations = _decision_and_citations(
        engine, clean_applicant.model_copy(update={"credit_score": 600})
    )
    report = validate_explanation(
        "Congratulations, you are pre-qualified and your loan is approved. [1]",
        decision,
        citations,
    )
    assert not report.ok
    assert len(report.violations) >= 2  # contradiction + commitment language


def test_commitment_language_is_caught(engine: RuleEngine, clean_applicant):
    decision, citations = _decision_and_citations(engine, clean_applicant)
    report = validate_explanation(
        "You are pre-qualified under policy version 2.0 and we guarantee this rate. [1]",
        decision,
        citations,
    )
    assert not report.ok
    assert any("guarantee" in violation for violation in report.violations)


def test_missing_and_invented_citations_are_caught(engine: RuleEngine, clean_applicant):
    decision, citations = _decision_and_citations(engine, clean_applicant)

    missing = validate_explanation(
        "You are pre-qualified under policy version 2.0.", decision, citations
    )
    assert "no citation markers were used" in missing.violations

    invented = validate_explanation(
        "You are pre-qualified under policy version 2.0. [1] [99]", decision, citations
    )
    assert any("invented citation marker" in violation for violation in invented.violations)


def test_wrong_policy_version_is_caught(engine: RuleEngine, clean_applicant):
    decision, citations = _decision_and_citations(engine, clean_applicant, version="2.0")
    report = validate_explanation(
        "You are pre-qualified under policy version 1.0. [1]", decision, citations
    )
    assert not report.ok
    assert any("policy version 1.0" in violation for violation in report.violations)


def test_empty_explanation_is_a_violation(engine: RuleEngine, clean_applicant):
    decision, citations = _decision_and_citations(engine, clean_applicant)
    assert validate_explanation("   ", decision, citations).violations == ["empty explanation"]


# ---------------------------------------------------------------------------
# Explainer behaviour without an API key
# ---------------------------------------------------------------------------
def test_explainer_falls_back_to_the_template_offline(engine: RuleEngine, clean_applicant):
    explainer = ClaudeExplainer()
    assert explainer.mode is ExplanationMode.DETERMINISTIC_TEMPLATE
    assert explainer.model is None

    decision, citations = _decision_and_citations(engine, clean_applicant)
    record = explainer.explain_sync(decision, citations, question="Am I eligible?")
    assert record.mode is ExplanationMode.DETERMINISTIC_TEMPLATE
    assert record.text.strip()
    assert record.error is None
    assert validate_explanation(record.text, decision, citations).ok


@pytest.mark.asyncio
async def test_streaming_yields_multiple_chunks(engine: RuleEngine, clean_applicant):
    explainer = ClaudeExplainer()
    decision, citations = _decision_and_citations(engine, clean_applicant)
    chunks = [chunk async for chunk in explainer.stream(decision, citations)]
    assert len(chunks) > 1
    assert "".join(chunks) == render_deterministic_explanation(decision, citations)
