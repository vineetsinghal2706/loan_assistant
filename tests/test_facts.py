"""Affordability arithmetic: amortisation, DTI, LTV and None-safety."""

from __future__ import annotations

import pytest

from app.rules.facts import (
    derive_facts,
    max_principal_for_payment,
    monthly_payment,
    round_facts,
)
from app.schemas import ApplicantProfile, LoanProduct

PERSONAL_ASSUMPTIONS = {
    "annual_interest_rate": 0.129,
    "default_term_months": 60,
    "stress_rate_increase": 0.02,
}


def test_monthly_payment_matches_annuity_formula():
    # 10,000 over 12 months at 12% nominal => 888.49 per month.
    payment = monthly_payment(10000, 0.12, 12)
    assert payment is not None
    assert abs(payment - 888.49) < 0.5


def test_zero_rate_is_straight_line():
    assert monthly_payment(12000, 0.0, 12) == pytest.approx(1000.0)


def test_payment_is_none_when_inputs_missing():
    assert monthly_payment(None, 0.12, 60) is None
    assert monthly_payment(10000, None, 60) is None
    assert monthly_payment(10000, 0.12, None) is None


def test_capacity_is_the_inverse_of_payment():
    principal = 25000
    payment = monthly_payment(principal, 0.089, 60)
    recovered = max_principal_for_payment(payment, 0.089, 60)
    assert recovered == pytest.approx(principal, rel=1e-6)


def test_derived_facts_for_personal_loan():
    applicant = ApplicantProfile(
        age=34,
        annual_income=90000,
        other_annual_income=6000,
        existing_monthly_debt=400,
        requested_amount=20000,
        loan_term_months=60,
        liquid_savings=15000,
    )
    facts = derive_facts(applicant, LoanProduct.PERSONAL_LOAN, PERSONAL_ASSUMPTIONS)

    assert facts["gross_annual_income"] == pytest.approx(96000)
    assert facts["monthly_income"] == pytest.approx(8000)
    assert facts["estimated_monthly_payment"] == pytest.approx(454.06, abs=1.0)
    assert facts["total_monthly_obligations"] == pytest.approx(854.06, abs=1.0)
    assert facts["dti_ratio"] == pytest.approx(854.06 / 8000, abs=0.001)
    # the stressed rate must produce a strictly higher payment and ratio
    assert facts["stressed_monthly_payment"] > facts["estimated_monthly_payment"]
    assert facts["stressed_dti_ratio"] > facts["dti_ratio"]
    assert facts["income_multiple_used"] == pytest.approx(20000 / 96000)
    assert facts["savings_buffer_multiple"] == pytest.approx(15000 / facts["total_monthly_obligations"])
    # no collateral on an unsecured product
    assert facts["ltv_ratio"] is None


def test_derived_facts_are_none_safe():
    facts = derive_facts(ApplicantProfile(), LoanProduct.PERSONAL_LOAN, PERSONAL_ASSUMPTIONS)
    for key in (
        "gross_annual_income",
        "monthly_income",
        "dti_ratio",
        "stressed_dti_ratio",
        "estimated_monthly_payment",
        "ltv_ratio",
    ):
        assert facts[key] is None, key
    # the assessment term always falls back to the policy default
    assert facts["loan_term_months"] == 60


def test_ltv_uses_the_right_collateral_per_product():
    mortgage = ApplicantProfile(requested_amount=360000, property_value=400000, down_payment=40000)
    facts = derive_facts(
        mortgage,
        LoanProduct.MORTGAGE,
        {"annual_interest_rate": 0.069, "default_term_months": 360},
    )
    assert facts["ltv_ratio"] == pytest.approx(0.9)
    assert facts["down_payment_pct"] == pytest.approx(0.1)

    auto = ApplicantProfile(requested_amount=25000, vehicle_value=32000)
    auto_facts = derive_facts(
        auto, LoanProduct.AUTO_LOAN, {"annual_interest_rate": 0.089, "default_term_months": 60}
    )
    assert auto_facts["ltv_ratio"] == pytest.approx(25000 / 32000)


def test_round_facts_is_stable():
    rounded = round_facts({"a": 1 / 3, "b": "text", "c": None, "d": True})
    assert rounded["a"] == 0.3333
    assert rounded["b"] == "text"
    assert rounded["c"] is None
    assert rounded["d"] is True
