"""Regression suite over hand-authored golden cases.

These assertions encode *policy intent*. If one of them changes, either the rule
pack changed deliberately (update the golden file in the same pull request and
explain why) or a regression has been introduced.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.rules.engine import RuleEngine
from app.schemas import ApplicantProfile, LoanProduct


def _ids(cases: List[Dict[str, Any]]) -> List[str]:
    return [case["id"] for case in cases]


def test_golden_file_is_complete(golden_cases):
    assert len(golden_cases) >= 10
    for case in golden_cases:
        assert set(case["expected"]) == {"1.0", "2.0"}, case["id"]
        assert case["product"] in {"PERSONAL_LOAN", "MORTGAGE", "AUTO_LOAN"}


def test_golden_outcomes(engine: RuleEngine, golden_cases):
    failures: List[str] = []
    for case in golden_cases:
        applicant = ApplicantProfile(**case["applicant"])
        product = LoanProduct(case["product"])
        for version, expectation in case["expected"].items():
            decision = engine.evaluate(
                applicant, product=product, policy_version=version
            )
            if decision.outcome.value != expectation["outcome"]:
                failures.append(
                    f"{case['id']} v{version}: expected {expectation['outcome']}, "
                    f"got {decision.outcome.value} "
                    f"(hard={decision.failed_hard_rules}, soft={decision.failed_soft_rules}, "
                    f"unknown={decision.unknown_rules})"
                )
                continue
            if set(decision.failed_hard_rules) != set(expectation["failed_hard"]):
                failures.append(
                    f"{case['id']} v{version}: mandatory failures "
                    f"{decision.failed_hard_rules} != {expectation['failed_hard']}"
                )
            if set(decision.failed_soft_rules) != set(expectation["failed_soft"]):
                failures.append(
                    f"{case['id']} v{version}: supporting failures "
                    f"{decision.failed_soft_rules} != {expectation['failed_soft']}"
                )
            if set(decision.unknown_rules) != set(expectation["unknown"]):
                failures.append(
                    f"{case['id']} v{version}: unknown criteria "
                    f"{decision.unknown_rules} != {expectation['unknown']}"
                )
    assert not failures, "\n".join(failures)


def test_golden_decisions_are_reproducible(engine: RuleEngine, golden_cases):
    """Re-running the same case must give byte-identical decision hashes."""
    for case in golden_cases:
        applicant = ApplicantProfile(**case["applicant"])
        product = LoanProduct(case["product"])
        for version in case["expected"]:
            first = engine.evaluate(applicant, product=product, policy_version=version)
            second = engine.evaluate(applicant, product=product, policy_version=version)
            assert first.decision_hash == second.decision_hash, f"{case['id']} v{version}"


def test_version_sensitive_cases_actually_differ(engine: RuleEngine, golden_cases):
    """The suite must keep at least a few cases where the version changes the answer."""
    differing = [
        case["id"]
        for case in golden_cases
        if case["expected"]["1.0"]["outcome"] != case["expected"]["2.0"]["outcome"]
    ]
    assert len(differing) >= 3, f"only {differing} differ across versions"


@pytest.mark.parametrize(
    "case_id,version,expected_rule",
    [
        ("B-credit-score-645", "2.0", "CR-2.1-CREDIT-SCORE"),
        ("C-employment-tenure-9-months", "2.0", "PL-3.2-EMPLOYMENT"),
        ("H-mortgage-ltv-tightened", "2.0", "MG-5.1-LTV"),
        ("K-bankruptcy-window-relaxed", "1.0", "CR-4.1-BANKRUPTCY"),
    ],
)
def test_named_criteria_are_the_reason(
    engine: RuleEngine, golden_cases, case_id, version, expected_rule
):
    case = next(item for item in golden_cases if item["id"] == case_id)
    decision = engine.evaluate(
        ApplicantProfile(**case["applicant"]),
        product=LoanProduct(case["product"]),
        policy_version=version,
    )
    assert expected_rule in decision.failed_hard_rules
    reason = next(r for r in decision.rule_results if r.rule_id == expected_rule)
    assert reason.policy_reference.policy_version == version
    assert reason.detail
