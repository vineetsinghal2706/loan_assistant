"""Derived facts used by the rule engine.

Every function is pure and `None`-safe: a missing applicant value produces a
`None` fact, which the condition evaluator reports as UNKNOWN rather than as a
failure. That distinction is what keeps "we don't know yet" from being
communicated to an applicant as "you were declined".
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

from app.schemas import ApplicantProfile, LoanProduct

MONTHS_PER_YEAR = 12


def monthly_payment(
    principal: Optional[float],
    annual_rate: Optional[float],
    months: Optional[int],
) -> Optional[float]:
    """Standard amortising payment. Returns None when inputs are unusable."""
    if principal is None or annual_rate is None or months is None:
        return None
    if principal <= 0 or months <= 0:
        return 0.0
    rate = annual_rate / MONTHS_PER_YEAR
    if rate <= 0:
        return principal / months
    factor = (1.0 + rate) ** (-months)
    denominator = 1.0 - factor
    if denominator <= 0:
        return None
    return principal * rate / denominator


def max_principal_for_payment(
    payment: Optional[float],
    annual_rate: Optional[float],
    months: Optional[int],
) -> Optional[float]:
    """Inverse of :func:`monthly_payment` - capacity for a given payment."""
    if payment is None or annual_rate is None or months is None:
        return None
    if payment <= 0 or months <= 0:
        return 0.0
    rate = annual_rate / MONTHS_PER_YEAR
    if rate <= 0:
        return payment * months
    factor = (1.0 + rate) ** (-months)
    return payment * (1.0 - factor) / rate


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "value", value)


def collateral_value(
    applicant: ApplicantProfile, product: LoanProduct
) -> Optional[float]:
    if product == LoanProduct.MORTGAGE:
        return applicant.property_value
    if product == LoanProduct.AUTO_LOAN:
        return applicant.vehicle_value
    return None


def derive_facts(
    applicant: ApplicantProfile,
    product: LoanProduct,
    assumptions: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build the fact dictionary that rule conditions are evaluated against."""

    rate = float(assumptions.get("annual_interest_rate", 0.0))
    stress_increase = float(assumptions.get("stress_rate_increase", 0.0))
    default_term = int(assumptions.get("default_term_months", 60))
    max_term = assumptions.get("max_term_months")

    gross_annual_income: Optional[float] = None
    if applicant.annual_income is not None:
        gross_annual_income = float(applicant.annual_income) + float(
            applicant.other_annual_income or 0.0
        )

    monthly_income = (
        gross_annual_income / MONTHS_PER_YEAR if gross_annual_income is not None else None
    )

    term_months = applicant.loan_term_months or default_term
    payment = monthly_payment(applicant.requested_amount, rate, term_months)
    stressed_payment = monthly_payment(
        applicant.requested_amount, rate + stress_increase, term_months
    )

    existing_debt = applicant.existing_monthly_debt
    total_obligations = (
        existing_debt + payment
        if existing_debt is not None and payment is not None
        else None
    )
    stressed_obligations = (
        existing_debt + stressed_payment
        if existing_debt is not None and stressed_payment is not None
        else None
    )

    security_value = collateral_value(applicant, product)
    ltv = _ratio(applicant.requested_amount, security_value)
    down_payment_pct = _ratio(applicant.down_payment, security_value)

    facts: Dict[str, Any] = {
        # identity
        "age": applicant.age,
        "residency_status": _enum_value(applicant.residency_status),
        # employment and income
        "employment_status": _enum_value(applicant.employment_status),
        "employment_months": applicant.employment_months,
        "gross_annual_income": gross_annual_income,
        "monthly_income": monthly_income,
        # credit
        "credit_score": applicant.credit_score,
        "credit_history_months": applicant.credit_history_months,
        "active_defaults": applicant.active_defaults,
        "has_prior_bankruptcy": applicant.has_prior_bankruptcy,
        "years_since_bankruptcy": applicant.years_since_bankruptcy,
        # facility
        "product": product.value,
        "requested_amount": applicant.requested_amount,
        "loan_term_months": term_months,
        "loan_term_requested": applicant.loan_term_months,
        "assumed_annual_rate": rate,
        "stressed_annual_rate": rate + stress_increase,
        "max_term_months": max_term,
        "estimated_monthly_payment": payment,
        "stressed_monthly_payment": stressed_payment,
        "existing_monthly_debt": existing_debt,
        "total_monthly_obligations": total_obligations,
        "dti_ratio": _ratio(total_obligations, monthly_income),
        "stressed_dti_ratio": _ratio(stressed_obligations, monthly_income),
        "income_multiple_used": _ratio(applicant.requested_amount, gross_annual_income),
        # security
        "collateral_value": security_value,
        "property_value": applicant.property_value,
        "vehicle_value": applicant.vehicle_value,
        "down_payment": applicant.down_payment,
        "down_payment_pct": down_payment_pct,
        "ltv_ratio": ltv,
        # buffers and verification
        "liquid_savings": applicant.liquid_savings,
        "savings_buffer_multiple": _ratio(applicant.liquid_savings, total_obligations),
        "reserves_multiple": _ratio(applicant.liquid_savings, payment),
        "documents_verified": applicant.documents_verified,
        "is_first_time_buyer": applicant.is_first_time_buyer,
    }
    return facts


def round_facts(facts: Mapping[str, Any], digits: int = 4) -> Dict[str, Any]:
    """Round floats so audit records and decision hashes stay reproducible."""
    rounded: Dict[str, Any] = {}
    for key, value in facts.items():
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                rounded[key] = None
            else:
                rounded[key] = round(value, digits)
        else:
            rounded[key] = value
    return rounded
