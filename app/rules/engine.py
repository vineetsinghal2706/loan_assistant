"""The deterministic eligibility rule engine.

This module - not the LLM - decides eligibility. Given the same applicant, the
same product and the same policy version it always returns the same outcome and
the same ``decision_hash``, which is what makes the regression suite and the
audit trail meaningful.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app import ENGINE_VERSION
from app.rules.facts import derive_facts, max_principal_for_payment, round_facts
from app.rules.predicates import ConditionOutcome, evaluate_condition
from app.rules.registry import PolicyRegistry, ProductConfig, Rule, get_registry
from app.schemas import (
    ApplicantProfile,
    EligibilityDecision,
    EligibilityOutcome,
    LoanProduct,
    PolicyReference,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    VersionComparisonEntry,
    VersionComparisonResponse,
)

HEADLINES = {
    EligibilityOutcome.ELIGIBLE: (
        "Pre-qualified: every criterion in the applicable DemoBank policy version is met."
    ),
    EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS: (
        "Pre-qualified subject to conditions: all mandatory criteria are met, but one or "
        "more supporting criteria need verification or credit officer review."
    ),
    EligibilityOutcome.NOT_ELIGIBLE: (
        "Not pre-qualified: at least one mandatory criterion in the applicable DemoBank "
        "policy version is not met."
    ),
    EligibilityOutcome.INSUFFICIENT_INFORMATION: (
        "Cannot complete pre-qualification yet: information required by a mandatory "
        "criterion is missing. This is not a decline."
    ),
}


def _floor_to(value: float, step: int = 100) -> float:
    return float(math.floor(value / step) * step)


class RuleEngine:
    """Evaluates an applicant against a versioned rule pack."""

    engine_version = ENGINE_VERSION

    def __init__(self, registry: Optional[PolicyRegistry] = None) -> None:
        self.registry = registry or get_registry()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(
        self,
        applicant: ApplicantProfile,
        product: LoanProduct = LoanProduct.PERSONAL_LOAN,
        policy_version: Optional[str] = None,
        as_of: Optional[date] = None,
    ) -> EligibilityDecision:
        pack = self.registry.resolve(policy_version=policy_version, as_of=as_of)
        config = pack.product(product)
        facts = derive_facts(applicant, product, config.assumptions)

        rule_results = [self._evaluate_rule(rule, facts) for rule in config.rules]

        failed_hard = [
            result.rule_id
            for result in rule_results
            if result.severity == RuleSeverity.HARD and result.status == RuleStatus.FAIL
        ]
        unknown_hard = [
            result.rule_id
            for result in rule_results
            if result.severity == RuleSeverity.HARD and result.status == RuleStatus.UNKNOWN
        ]
        failed_soft = [
            result.rule_id
            for result in rule_results
            if result.severity == RuleSeverity.SOFT and result.status == RuleStatus.FAIL
        ]
        unknown_soft = [
            result.rule_id
            for result in rule_results
            if result.severity == RuleSeverity.SOFT and result.status == RuleStatus.UNKNOWN
        ]

        outcome = self._outcome(failed_hard, unknown_hard, failed_soft, unknown_soft)
        max_amount = self._max_eligible_amount(config, facts)
        rounded = round_facts(facts)
        if max_amount is not None:
            rounded["max_eligible_amount"] = max_amount

        decision = EligibilityDecision(
            decision_id=f"dec_{uuid.uuid4().hex[:12]}",
            outcome=outcome,
            headline=HEADLINES[outcome],
            product=product,
            policy_version=pack.policy_version,
            policy_effective_from=pack.effective_from,
            policy_effective_to=pack.effective_to,
            engine_version=self.engine_version,
            evaluated_at=datetime.now(timezone.utc),
            rule_results=rule_results,
            failed_hard_rules=failed_hard,
            failed_soft_rules=failed_soft,
            unknown_rules=unknown_hard + unknown_soft,
            computed_facts=rounded,
            estimated_monthly_payment=(
                round(facts["estimated_monthly_payment"], 2)
                if facts.get("estimated_monthly_payment") is not None
                else None
            ),
            max_eligible_amount=max_amount,
            requested_amount=applicant.requested_amount,
            required_actions=self._required_actions(rule_results),
        )
        decision.decision_hash = self.decision_hash(decision)
        return decision

    def compare_versions(
        self,
        applicant: ApplicantProfile,
        product: LoanProduct = LoanProduct.PERSONAL_LOAN,
        versions: Optional[List[str]] = None,
    ) -> VersionComparisonResponse:
        """Run the same applicant through several policy versions."""
        target_versions = versions or self.registry.versions
        entries: List[VersionComparisonEntry] = []
        decisions: List[EligibilityDecision] = []
        for version in target_versions:
            decision = self.evaluate(applicant, product=product, policy_version=version)
            decisions.append(decision)
            entries.append(
                VersionComparisonEntry(
                    policy_version=decision.policy_version,
                    outcome=decision.outcome,
                    headline=decision.headline,
                    failed_hard_rules=decision.failed_hard_rules,
                    failed_soft_rules=decision.failed_soft_rules,
                    unknown_rules=decision.unknown_rules,
                    max_eligible_amount=decision.max_eligible_amount,
                    decision_hash=decision.decision_hash,
                )
            )
        differences = self._describe_differences(decisions)
        return VersionComparisonResponse(
            product=product,
            entries=entries,
            differences=differences,
            identical=len({entry.outcome for entry in entries}) == 1 and not differences,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _evaluate_rule(self, rule: Rule, facts: Mapping[str, Any]) -> RuleResult:
        reference = PolicyReference(**dict(rule.policy_reference))

        if rule.applies_when is not None:
            gate = evaluate_condition(rule.applies_when, facts)
            if gate.satisfied is False:
                return RuleResult(
                    rule_id=rule.id,
                    title=rule.title,
                    category=rule.category,
                    severity=rule.severity,
                    status=RuleStatus.NOT_APPLICABLE,
                    detail=f"Criterion does not apply: {gate.detail}",
                    message="This criterion does not apply to the enquiry as stated.",
                    policy_reference=reference,
                    observed=self._observed(rule, facts, gate),
                    threshold=rule.threshold,
                )

        outcome: ConditionOutcome = evaluate_condition(rule.condition, facts)

        if outcome.satisfied is True:
            status = RuleStatus.PASS
            message = rule.message_pass or f"{rule.title}: satisfied."
        elif outcome.satisfied is False:
            status = RuleStatus.FAIL
            message = rule.message_fail or f"{rule.title}: not satisfied."
        else:
            status = RuleStatus.UNKNOWN
            message = (
                rule.message_unknown
                or f"{rule.title}: cannot be assessed until the missing data is supplied."
            )

        return RuleResult(
            rule_id=rule.id,
            title=rule.title,
            category=rule.category,
            severity=rule.severity,
            status=status,
            detail=outcome.detail,
            message=message,
            missing_fields=outcome.missing_fields,
            policy_reference=reference,
            observed=self._observed(rule, facts, outcome),
            threshold=rule.threshold,
        )

    @staticmethod
    def _observed(
        rule: Rule, facts: Mapping[str, Any], outcome: ConditionOutcome
    ) -> Dict[str, Any]:
        observed: Dict[str, Any] = dict(outcome.observed)
        for name in rule.observe:
            observed[name] = facts.get(name)
        return round_facts(observed)

    @staticmethod
    def _outcome(
        failed_hard: List[str],
        unknown_hard: List[str],
        failed_soft: List[str],
        unknown_soft: List[str],
    ) -> EligibilityOutcome:
        if failed_hard:
            return EligibilityOutcome.NOT_ELIGIBLE
        if unknown_hard:
            return EligibilityOutcome.INSUFFICIENT_INFORMATION
        if failed_soft or unknown_soft:
            return EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS
        return EligibilityOutcome.ELIGIBLE

    @staticmethod
    def _max_eligible_amount(
        config: ProductConfig, facts: Mapping[str, Any]
    ) -> Optional[float]:
        """Lowest of product cap, income multiple cap, DTI capacity and LTV cap.

        Implements section CR-5.1 of the synthetic Credit and Affordability
        Standards.
        """
        limits = config.limits
        assumptions = config.assumptions
        rate = float(assumptions.get("annual_interest_rate", 0.0))
        stress = float(assumptions.get("stress_rate_increase", 0.0))
        term = int(facts.get("loan_term_months") or assumptions.get("default_term_months", 60))

        candidates: List[float] = []

        if limits.get("max_amount") is not None:
            candidates.append(float(limits["max_amount"]))

        income = facts.get("gross_annual_income")
        if limits.get("income_multiple") is not None and income:
            candidates.append(float(limits["income_multiple"]) * float(income))

        monthly_income = facts.get("monthly_income")
        existing_debt = facts.get("existing_monthly_debt")
        if monthly_income and existing_debt is not None:
            if limits.get("max_dti") is not None:
                capacity = float(limits["max_dti"]) * monthly_income - existing_debt
                principal = max_principal_for_payment(max(capacity, 0.0), rate, term)
                if principal is not None:
                    candidates.append(principal)
            if limits.get("max_stressed_dti") is not None:
                capacity = float(limits["max_stressed_dti"]) * monthly_income - existing_debt
                principal = max_principal_for_payment(
                    max(capacity, 0.0), rate + stress, term
                )
                if principal is not None:
                    candidates.append(principal)

        security = facts.get("collateral_value")
        if limits.get("max_ltv") is not None and security:
            candidates.append(float(limits["max_ltv"]) * float(security))

        if not candidates:
            return None
        return max(_floor_to(min(candidates)), 0.0)

    @staticmethod
    def _required_actions(rule_results: List[RuleResult]) -> List[str]:
        actions: List[str] = []
        for result in rule_results:
            if result.status not in {RuleStatus.FAIL, RuleStatus.UNKNOWN}:
                continue
            if result.status == RuleStatus.UNKNOWN and result.missing_fields:
                readable = ", ".join(
                    name.replace("_", " ") for name in result.missing_fields
                )
                action = (
                    f"Provide {readable} so criterion {result.rule_id} "
                    f"({result.policy_reference.section}) can be assessed."
                )
            else:
                action = (
                    f"Criterion {result.rule_id} ({result.policy_reference.section}): "
                    f"{result.detail}"
                )
            if action not in actions:
                actions.append(action)
        return actions

    @staticmethod
    def decision_hash(decision: EligibilityDecision) -> str:
        """Stable fingerprint of a decision: excludes ids and timestamps."""
        payload = {
            "engine_version": decision.engine_version,
            "policy_version": decision.policy_version,
            "product": decision.product.value,
            "outcome": decision.outcome.value,
            "max_eligible_amount": decision.max_eligible_amount,
            "estimated_monthly_payment": decision.estimated_monthly_payment,
            "rules": [
                {
                    "id": result.rule_id,
                    "status": result.status.value,
                    "severity": result.severity.value,
                }
                for result in decision.rule_results
            ],
            "facts": {
                key: value
                for key, value in sorted(decision.computed_facts.items())
                if isinstance(value, (int, float, bool, str)) or value is None
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _describe_differences(decisions: List[EligibilityDecision]) -> List[str]:
        differences: List[str] = []
        for previous, current in zip(decisions, decisions[1:]):
            if previous.outcome != current.outcome:
                differences.append(
                    f"Outcome changed from {previous.outcome.value} under policy "
                    f"{previous.policy_version} to {current.outcome.value} under policy "
                    f"{current.policy_version}."
                )
            before = {r.rule_id: r.status for r in previous.rule_results}
            for result in current.rule_results:
                old = before.get(result.rule_id)
                if old is None:
                    if result.status in {RuleStatus.FAIL, RuleStatus.UNKNOWN}:
                        differences.append(
                            f"Policy {current.policy_version} adds criterion "
                            f"{result.rule_id} ({result.policy_reference.section}), "
                            f"which is {result.status.value} for this applicant."
                        )
                    continue
                if old != result.status:
                    differences.append(
                        f"Criterion {result.rule_id} "
                        f"({result.policy_reference.section}) moved from {old.value} to "
                        f"{result.status.value} between policy {previous.policy_version} "
                        f"and {current.policy_version}."
                    )
            after_ids = {r.rule_id for r in current.rule_results}
            for rule_id in before:
                if rule_id not in after_ids:
                    differences.append(
                        f"Criterion {rule_id} present in policy {previous.policy_version} "
                        f"was removed in policy {current.policy_version}."
                    )
        return differences


def summarise_decision(decision: EligibilityDecision) -> Dict[str, Any]:
    """Compact dict used for prompts, audit payloads and the UI."""
    return {
        "outcome": decision.outcome.value,
        "headline": decision.headline,
        "product": decision.product.value,
        "policy_version": decision.policy_version,
        "policy_effective_from": decision.policy_effective_from.isoformat(),
        "policy_effective_to": (
            decision.policy_effective_to.isoformat() if decision.policy_effective_to else None
        ),
        "engine_version": decision.engine_version,
        "decision_hash": decision.decision_hash,
        "requested_amount": decision.requested_amount,
        "estimated_monthly_payment": decision.estimated_monthly_payment,
        "max_eligible_amount": decision.max_eligible_amount,
        "failed_hard_rules": decision.failed_hard_rules,
        "failed_soft_rules": decision.failed_soft_rules,
        "unknown_rules": decision.unknown_rules,
        "required_actions": decision.required_actions,
        "rules": [
            {
                "id": result.rule_id,
                "title": result.title,
                "severity": result.severity.value,
                "status": result.status.value,
                "detail": result.detail,
                "section": result.policy_reference.section,
                "document_id": result.policy_reference.document_id,
            }
            for result in decision.rule_results
        ],
    }


def rule_sections(decision: EligibilityDecision, only_relevant: bool = True) -> List[Tuple[str, str]]:
    """(document_id, section) pairs to retrieve citations for."""
    pairs: List[Tuple[str, str]] = []
    for result in decision.rule_results:
        if only_relevant and result.status not in {
            RuleStatus.FAIL,
            RuleStatus.UNKNOWN,
        }:
            continue
        pair = (result.policy_reference.document_id, result.policy_reference.section)
        if pair not in pairs:
            pairs.append(pair)
    if not pairs:
        for result in decision.rule_results:
            if result.severity == RuleSeverity.HARD:
                pair = (result.policy_reference.document_id, result.policy_reference.section)
                if pair not in pairs:
                    pairs.append(pair)
    return pairs
