"""The deterministic engine is the source of truth for eligibility."""

from __future__ import annotations

import pytest

from app.rules.engine import RuleEngine, rule_sections, summarise_decision
from app.schemas import (
    ApplicantProfile,
    EligibilityOutcome,
    LoanProduct,
    RuleSeverity,
    RuleStatus,
)


def test_clean_applicant_is_eligible_under_both_versions(engine: RuleEngine, clean_applicant):
    for version in ("1.0", "2.0"):
        decision = engine.evaluate(
            clean_applicant, product=LoanProduct.PERSONAL_LOAN, policy_version=version
        )
        assert decision.outcome is EligibilityOutcome.ELIGIBLE, version
        assert decision.failed_hard_rules == []
        assert decision.failed_soft_rules == []
        assert decision.unknown_rules == []
        assert decision.policy_version == version


def test_hard_failure_produces_not_eligible(engine: RuleEngine, clean_applicant):
    applicant = clean_applicant.model_copy(update={"age": 19})
    decision = engine.evaluate(applicant, policy_version="2.0")
    assert decision.outcome is EligibilityOutcome.NOT_ELIGIBLE
    assert "PL-2.1-AGE" in decision.failed_hard_rules
    assert decision.required_actions


def test_soft_failure_produces_conditional(engine: RuleEngine, clean_applicant):
    applicant = clean_applicant.model_copy(update={"documents_verified": False})
    decision = engine.evaluate(applicant, policy_version="2.0")
    assert decision.outcome is EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS
    assert decision.failed_soft_rules == ["DOC-2.1-VERIFICATION"]
    assert decision.failed_hard_rules == []


def test_missing_hard_input_is_insufficient_information_not_a_decline(
    engine: RuleEngine, clean_applicant
):
    applicant = clean_applicant.model_copy(update={"credit_score": None})
    decision = engine.evaluate(applicant, policy_version="2.0")
    assert decision.outcome is EligibilityOutcome.INSUFFICIENT_INFORMATION
    assert decision.failed_hard_rules == []
    assert "CR-2.1-CREDIT-SCORE" in decision.unknown_rules
    unknown = next(r for r in decision.rule_results if r.rule_id == "CR-2.1-CREDIT-SCORE")
    assert unknown.status is RuleStatus.UNKNOWN
    assert unknown.missing_fields == ["credit_score"]
    assert any("credit score" in action for action in decision.required_actions)


def test_applies_when_marks_rules_not_applicable(engine: RuleEngine, clean_applicant):
    decision = engine.evaluate(clean_applicant, policy_version="2.0")
    self_employed_rule = next(
        r for r in decision.rule_results if r.rule_id == "PL-3.4-SELF-EMPLOYED-HISTORY"
    )
    assert self_employed_rule.status is RuleStatus.NOT_APPLICABLE

    applicant = clean_applicant.model_copy(
        update={"employment_status": "SELF_EMPLOYED", "employment_months": 14}
    )
    decision = engine.evaluate(applicant, policy_version="2.0")
    rule = next(r for r in decision.rule_results if r.rule_id == "PL-3.4-SELF-EMPLOYED-HISTORY")
    assert rule.status is RuleStatus.FAIL
    assert rule.severity is RuleSeverity.SOFT
    assert decision.outcome is EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS


def test_decision_hash_is_stable_and_sensitive(engine: RuleEngine, clean_applicant):
    first = engine.evaluate(clean_applicant, policy_version="2.0")
    second = engine.evaluate(clean_applicant, policy_version="2.0")
    assert first.decision_hash == second.decision_hash
    assert first.decision_id != second.decision_id  # ids are per-evaluation

    changed = engine.evaluate(
        clean_applicant.model_copy(update={"requested_amount": 21000}), policy_version="2.0"
    )
    assert changed.decision_hash != first.decision_hash

    other_version = engine.evaluate(clean_applicant, policy_version="1.0")
    assert other_version.decision_hash != first.decision_hash


def test_every_rule_result_carries_a_citable_clause(engine: RuleEngine, clean_applicant):
    decision = engine.evaluate(clean_applicant, policy_version="2.0")
    assert decision.rule_results
    for result in decision.rule_results:
        assert result.policy_reference.document_id
        assert result.policy_reference.section
        assert result.policy_reference.policy_version == "2.0"


def test_capacity_respects_the_lowest_cap(engine: RuleEngine, clean_applicant):
    decision = engine.evaluate(clean_applicant, policy_version="2.0")
    # v2.0 caps the personal loan at 60,000 and 4.5x income (405,000 here),
    # so the product cap or DTI capacity must bind.
    assert decision.max_eligible_amount is not None
    assert decision.max_eligible_amount <= 60000
    assert decision.max_eligible_amount >= decision.requested_amount

    low_income = clean_applicant.model_copy(
        update={"annual_income": 30000, "requested_amount": 5000, "liquid_savings": 6000}
    )
    capped = engine.evaluate(low_income, policy_version="2.0")
    assert capped.max_eligible_amount <= 4.5 * 30000


def test_stressed_affordability_only_exists_in_v2(engine: RuleEngine, clean_applicant):
    v1_ids = {r.rule_id for r in engine.evaluate(clean_applicant, policy_version="1.0").rule_results}
    v2_ids = {r.rule_id for r in engine.evaluate(clean_applicant, policy_version="2.0").rule_results}
    assert "CR-3.3-STRESSED-DTI" not in v1_ids
    assert "CR-3.3-STRESSED-DTI" in v2_ids
    assert "PL-5.2-SAVINGS-BUFFER" in v2_ids


def test_mortgage_needs_property_value(engine: RuleEngine):
    applicant = ApplicantProfile(
        age=32,
        residency_status="CITIZEN",
        employment_status="FULL_TIME",
        employment_months=36,
        annual_income=130000,
        existing_monthly_debt=500,
        credit_score=725,
        credit_history_months=96,
        active_defaults=0,
        has_prior_bankruptcy=False,
        liquid_savings=30000,
        documents_verified=True,
        requested_amount=360000,
        loan_term_months=360,
    )
    decision = engine.evaluate(applicant, product=LoanProduct.MORTGAGE, policy_version="2.0")
    assert decision.outcome is EligibilityOutcome.INSUFFICIENT_INFORMATION
    assert "MG-5.1-LTV" in decision.unknown_rules


def test_summarise_and_rule_sections(engine: RuleEngine, clean_applicant):
    decision = engine.evaluate(
        clean_applicant.model_copy(update={"credit_score": 600}), policy_version="2.0"
    )
    summary = summarise_decision(decision)
    assert summary["outcome"] == "NOT_ELIGIBLE"
    assert summary["policy_version"] == "2.0"
    assert any(rule["id"] == "CR-2.1-CREDIT-SCORE" for rule in summary["rules"])

    sections = rule_sections(decision)
    assert ("DB-CR", "CR-2.1") in sections


def test_unknown_product_for_version_raises(engine: RuleEngine, clean_applicant):
    from app.rules.registry import ProductNotSupported

    pack = engine.registry.get("2.0")
    with pytest.raises(ProductNotSupported):
        pack.product("STUDENT_LOAN")
