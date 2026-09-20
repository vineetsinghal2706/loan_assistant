"""Policy versioning: resolution, effective dates, and explained differences."""

from __future__ import annotations

from datetime import date

import pytest

from app.rules.engine import RuleEngine
from app.rules.registry import PolicyRegistry, PolicyVersionNotFound, version_sort_key
from app.schemas import EligibilityOutcome, LoanProduct


def test_both_versions_are_registered(registry: PolicyRegistry):
    assert registry.versions == ["1.0", "2.0"]
    assert registry.get("1.0").status == "superseded"
    assert registry.get("2.0").status == "active"
    assert registry.latest().policy_version == "2.0"


def test_version_sort_key_orders_numerically():
    assert version_sort_key("1.0") < version_sort_key("2.0")
    assert version_sort_key("2.0") < version_sort_key("10.1")


def test_effective_windows_do_not_overlap(registry: PolicyRegistry):
    v1 = registry.get("1.0")
    v2 = registry.get("2.0")
    assert v1.effective_to is not None
    assert v2.effective_from > v1.effective_to
    assert v2.effective_to is None


@pytest.mark.parametrize(
    "as_of,expected",
    [
        (date(2024, 5, 1), "1.0"),
        (date(2025, 6, 30), "1.0"),
        (date(2025, 7, 1), "2.0"),
        (date(2026, 1, 15), "2.0"),
    ],
)
def test_resolution_by_enquiry_date(registry: PolicyRegistry, as_of, expected):
    assert registry.resolve(as_of=as_of).policy_version == expected


def test_explicit_version_beats_as_of(registry: PolicyRegistry):
    pack = registry.resolve(policy_version="1.0", as_of=date(2026, 1, 1))
    assert pack.policy_version == "1.0"


def test_default_version_used_when_nothing_specified(registry: PolicyRegistry):
    assert registry.resolve().policy_version == "2.0"


def test_unknown_version_and_unreachable_date(registry: PolicyRegistry):
    with pytest.raises(PolicyVersionNotFound):
        registry.get("9.9")
    with pytest.raises(PolicyVersionNotFound):
        registry.resolve(as_of=date(1999, 1, 1))


def test_change_log_is_published(registry: PolicyRegistry):
    info = registry.info("2.0")
    assert info.change_log
    assert any("660" in entry for entry in info.change_log)
    assert set(info.products) == {"PERSONAL_LOAN", "MORTGAGE", "AUTO_LOAN"}


def test_same_applicant_different_outcome_across_versions(engine: RuleEngine, clean_applicant):
    borderline = clean_applicant.model_copy(update={"credit_score": 645})
    comparison = engine.compare_versions(borderline, product=LoanProduct.PERSONAL_LOAN)

    by_version = {entry.policy_version: entry for entry in comparison.entries}
    assert by_version["1.0"].outcome is EligibilityOutcome.ELIGIBLE
    assert by_version["2.0"].outcome is EligibilityOutcome.NOT_ELIGIBLE
    assert not comparison.identical
    assert any("CR-2.1-CREDIT-SCORE" in difference for difference in comparison.differences)
    assert any("Outcome changed" in difference for difference in comparison.differences)


def test_comparison_reports_added_criteria(engine: RuleEngine, clean_applicant):
    # v2.0 adds CR-3.3 and PL-5.2; make PL-5.2 fail so it is reported as added-and-failing.
    applicant = clean_applicant.model_copy(update={"liquid_savings": 100})
    comparison = engine.compare_versions(applicant, product=LoanProduct.PERSONAL_LOAN)
    assert any(
        "adds criterion PL-5.2-SAVINGS-BUFFER" in difference
        for difference in comparison.differences
    )


def test_identical_when_nothing_changes_for_this_applicant(
    engine: RuleEngine, clean_applicant
):
    comparison = engine.compare_versions(clean_applicant, product=LoanProduct.PERSONAL_LOAN)
    assert {entry.outcome for entry in comparison.entries} == {EligibilityOutcome.ELIGIBLE}


def test_rule_packs_only_cite_their_own_version(registry: PolicyRegistry):
    for version in registry.versions:
        pack = registry.get(version)
        for config in pack.products.values():
            for rule in config.rules:
                assert rule.policy_reference["policy_version"] == version
                assert rule.policy_reference["document_id"] in pack.documents
