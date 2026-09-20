"""Applicant extraction must be accurate, provenanced and honest about gaps."""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.extraction.applicant_extractor import (
    extract_from_bytes,
    extract_from_path,
    merge_profiles,
)
from app.extraction.parsers import UnsupportedDocument, parse_bytes
from app.schemas import ApplicantProfile, EmploymentStatus, ResidencyStatus


def sample(name: str):
    return get_settings().applicants_dir / name


def test_extracts_the_clean_personal_loan_pack():
    result = extract_from_path(sample("applicant_a_clean_personal_loan.md"))
    applicant = result.applicant

    assert applicant.applicant_reference == "DEMO-A-1042"
    assert applicant.age == 34
    assert applicant.residency_status is ResidencyStatus.CITIZEN
    assert applicant.employment_status is EmploymentStatus.FULL_TIME
    assert applicant.employment_months == 48
    assert applicant.annual_income == 90000
    assert applicant.existing_monthly_debt == 400
    assert applicant.credit_score == 720
    assert applicant.credit_history_months == 120
    assert applicant.active_defaults == 0
    assert applicant.has_prior_bankruptcy is False
    assert applicant.liquid_savings == 15000
    assert applicant.documents_verified is True
    assert applicant.requested_amount == 20000
    assert applicant.loan_term_months == 60
    assert result.missing_fields == []


def test_every_extracted_field_has_provenance():
    result = extract_from_path(sample("applicant_a_clean_personal_loan.md"))
    assert result.fields
    for field in result.fields:
        assert field.provenance is not None
        assert field.provenance.source_document.endswith(".md")
        assert field.provenance.page == 1
        assert field.provenance.snippet
        assert 0.0 < field.confidence <= 1.0
    score = next(field for field in result.fields if field.field_name == "credit_score")
    assert "720" in score.provenance.snippet


def test_extracts_mortgage_specific_fields():
    result = extract_from_path(sample("applicant_d_mortgage_first_time_buyer.md"))
    applicant = result.applicant
    assert applicant.requested_amount == 360000
    assert applicant.loan_term_months == 360
    assert applicant.property_value == 400000
    assert applicant.down_payment == 40000
    assert applicant.is_first_time_buyer is True
    assert applicant.credit_score == 725


def test_borderline_and_thin_file_packs():
    borderline = extract_from_path(sample("applicant_b_borderline_credit_score.md")).applicant
    assert borderline.credit_score == 645
    assert borderline.residency_status is ResidencyStatus.PERMANENT_RESIDENT

    thin = extract_from_path(sample("applicant_c_thin_file_conditional.md")).applicant
    assert thin.credit_history_months == 12
    assert thin.documents_verified is False


def test_missing_fields_are_reported_not_guessed():
    text = b"""DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY
Applicant Reference: DEMO-X-1
Age: 29
Gross Annual Income: 42,000
"""
    result = extract_from_bytes("partial.txt", text)
    assert result.applicant.age == 29
    assert result.applicant.annual_income == 42000
    assert result.applicant.credit_score is None
    for expected_missing in ("credit_score", "employment_status", "requested_amount"):
        assert expected_missing in result.missing_fields


def test_json_and_snake_case_labels_are_supported():
    payload = json.dumps(
        {
            "applicant_reference": "DEMO-J-9",
            "age": 44,
            "annual_income": 61000,
            "credit_score": 690,
            "employment_status": "self employed",
            "employment_months": 40,
            "existing_monthly_debt": 220,
            "requested_amount": 12000,
        }
    ).encode("utf-8")
    result = extract_from_bytes("applicant.json", payload)
    assert result.parser == "json"
    assert result.applicant.credit_score == 690
    assert result.applicant.employment_status is EmploymentStatus.SELF_EMPLOYED
    assert result.applicant.requested_amount == 12000


def test_invalid_values_are_dropped_with_a_warning():
    text = b"Age: 400\nGross Annual Income: 50,000\n"
    result = extract_from_bytes("bad.txt", text)
    assert result.applicant.age is None
    assert result.applicant.annual_income == 50000
    assert any("age" in warning for warning in result.warnings)


def test_unsupported_file_type_is_rejected():
    with pytest.raises(UnsupportedDocument):
        parse_bytes("applicant.docx", b"binary")


def test_merge_profiles_prefers_the_overlay():
    base = ApplicantProfile(age=30, annual_income=50000, credit_score=700)
    overlay = ApplicantProfile(annual_income=60000)
    merged = merge_profiles(base, overlay)
    assert merged.annual_income == 60000
    assert merged.age == 30
    assert merged.credit_score == 700


def test_extraction_can_overlay_a_base_profile():
    base = ApplicantProfile(
        applicant_reference="FORM", requested_amount=9999, documents_verified=False
    )
    result = extract_from_path(sample("applicant_a_clean_personal_loan.md"), base=base)
    # document values win over the form values they overlap
    assert result.applicant.requested_amount == 20000
    assert result.applicant.documents_verified is True
