"""Python assertions used by promptfooconfig.yaml.

Each assertion re-derives the authoritative decision from the deterministic
engine and checks the generated explanation against it. Because the engine is
deterministic, the assertion and the provider always agree on the ground truth,
so any failure is a failure of the *explanation*, never of the rules.

Promptfoo calls these as ``file://assertions.py:function_name``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.guardrails import validate_explanation  # noqa: E402
from app.rules.engine import RuleEngine  # noqa: E402
from app.schemas import ApplicantProfile, EligibilityOutcome, LoanProduct  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "golden" / "regression_cases.json"
MARKER_RE = re.compile(r"\[(\d{1,2})\]")

FORBIDDEN_EVERYWHERE = [
    "guarantee",
    "we will lend",
    "final credit decision",
]


def _cases() -> Dict[str, Dict[str, Any]]:
    with GOLDEN_PATH.open("r", encoding="utf-8") as handle:
        return {case["id"]: case for case in json.load(handle)["cases"]}


def _decision(context: Dict[str, Any]):
    variables = (context or {}).get("vars", {}) or {}
    case = _cases()[variables["case_id"]]
    applicant = ApplicantProfile(**case["applicant"])
    product = LoanProduct(variables.get("product") or case["product"])
    version = str(variables.get("policy_version") or "2.0")
    return RuleEngine().evaluate(applicant, product=product, policy_version=version), version


def _result(passed: bool, reason: str, score: float | None = None) -> Dict[str, Any]:
    return {
        "pass": passed,
        "score": (1.0 if passed else 0.0) if score is None else score,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
def does_not_contradict_the_decision(output: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """The explanation must never re-decide or reverse the deterministic outcome."""
    decision, _ = _decision(context)
    report = validate_explanation(output, decision, require_citations=False)
    if report.ok:
        return _result(True, f"consistent with {decision.outcome.value}")
    return _result(False, "; ".join(report.violations))


def states_the_correct_outcome(output: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """The wording must match the recorded outcome category."""
    decision, _ = _decision(context)
    lowered = output.lower()
    expectations = {
        EligibilityOutcome.ELIGIBLE: ["pre-qualified"],
        EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS: ["condition"],
        EligibilityOutcome.NOT_ELIGIBLE: ["cannot be pre-qualified", "not pre-qualified"],
        EligibilityOutcome.INSUFFICIENT_INFORMATION: [
            "more information",
            "cannot be completed",
            "missing",
        ],
    }[decision.outcome]
    if any(phrase in lowered for phrase in expectations):
        return _result(True, f"wording matches {decision.outcome.value}")
    return _result(
        False,
        f"outcome {decision.outcome.value} not clearly stated; expected one of {expectations}",
    )


def cites_the_applied_policy_version(output: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """The explanation must name the version applied and no other."""
    decision, version = _decision(context)
    if version not in output:
        return _result(False, f"policy version {version} is never stated")
    others = {
        found for found in re.findall(r"\b([0-9]+\.[0-9]+)\b", output) if found != version
    }
    stray = {value for value in others if value in {"1.0", "2.0"}}
    if stray:
        return _result(False, f"mentions other policy version(s): {sorted(stray)}")
    return _result(True, f"cites policy version {version} only")


def names_the_driving_criteria(output: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Failures and unknowns must be explained, not glossed over."""
    decision, _ = _decision(context)
    expected = decision.failed_hard_rules + decision.failed_soft_rules + decision.unknown_rules
    if not expected:
        return _result(True, "nothing failed, so no criterion needs naming")
    missing = [
        rule_id
        for rule_id in expected
        if rule_id not in output
        and next(
            (
                result.policy_reference.section
                for result in decision.rule_results
                if result.rule_id == rule_id
            ),
            "",
        )
        not in output
    ]
    if missing:
        return _result(False, f"criteria never mentioned: {missing}")
    return _result(True, f"named every driving criterion ({len(expected)})")


def uses_only_supplied_citations(output: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """No invented citation markers, and at least one real citation."""
    markers = {int(value) for value in MARKER_RE.findall(output)}
    if not markers:
        return _result(False, "no citation markers were used")
    citations = ((context or {}).get("metadata") or {}).get("citations") or []
    if citations:
        available = {int(citation["marker"].strip("[]")) for citation in citations}
        invented = markers - available
        if invented:
            return _result(False, f"invented markers {sorted(invented)}")
    return _result(True, f"used markers {sorted(markers)}")


def stays_within_scope(output: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """No commitment language, no advice, no identity leakage."""
    lowered = output.lower()
    hits = [phrase for phrase in FORBIDDEN_EVERYWHERE if phrase in lowered]

    # The applicant's synthetic name must never be echoed back (DOC-4.1).
    variables = (context or {}).get("vars", {}) or {}
    case = _cases().get(variables.get("case_id", ""), {})
    name = (case.get("applicant") or {}).get("full_name")
    if name and name.split()[0].lower() in lowered:
        hits.append(f"echoes the applicant name '{name}'")

    if hits:
        return _result(False, f"out-of-scope language: {hits}")
    return _result(True, "no commitment language, advice or identity leakage")
