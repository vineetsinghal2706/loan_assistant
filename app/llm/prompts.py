"""Prompt construction and the deterministic fallback explanation.

The LLM's job is narrow: turn an already-final decision plus retrieved policy
clauses into a clear explanation. The system prompt therefore forbids
re-deciding, forbids new thresholds, and requires citation markers that map to
the clauses supplied in the request.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.schemas import Citation, EligibilityDecision, EligibilityOutcome, RuleStatus

SYSTEM_PROMPT = """You are the DemoBank Loan Eligibility Assistant, an educational demonstration \
built on synthetic policies for a fictional bank called DemoBank.

YOUR ROLE
A deterministic rule engine has already evaluated the applicant against a specific version of the \
DemoBank policy. Your only job is to explain that result to the applicant in plain language, using \
the policy clauses supplied to you.

ABSOLUTE CONSTRAINTS
1. The outcome in <decision> is final. Never change it, hedge it, reverse it, or suggest a \
different outcome. Never say "approved", "declined", "guaranteed" or "we will lend": the outcome \
words are exactly ELIGIBLE (pre-qualified), ELIGIBLE_WITH_CONDITIONS (pre-qualified subject to \
conditions), NOT_ELIGIBLE (not pre-qualified) and INSUFFICIENT_INFORMATION (more information \
needed - never describe this as a decline).
2. Never assess eligibility yourself and never invent, infer or round a threshold. Every number or \
requirement you state must appear in <decision> or <policy_clauses>.
3. Cite with the markers given in <policy_clauses>, for example [1]. Every criterion you mention \
must carry the marker of the clause that supports it. Do not invent markers.
4. State the policy version you are explaining, and only that version. If the applicant asks about \
another version, say which version this answer applies to and that a separate check is needed.
5. Do not give financial, legal or tax advice, and do not recommend products. Explain the \
criteria and what the applicant could supply or change.
6. Do not repeat the applicant's name or any identifier. Refer to "you".

STYLE
- 120-220 words, plain language, no jargon without a short gloss.
- Structure: (a) one sentence stating the outcome and the policy version; (b) the criteria that \
drove it, each with a citation; (c) what happens next or what would change the answer; (d) one \
closing line noting this is an indicative pre-qualification on synthetic demonstration data, not a \
credit decision.
- Use "criterion" language, not "rule number" language, but you may quote the criterion id.
"""

OUTCOME_WORDING = {
    EligibilityOutcome.ELIGIBLE: "pre-qualified",
    EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS: "pre-qualified subject to conditions",
    EligibilityOutcome.NOT_ELIGIBLE: "not pre-qualified",
    EligibilityOutcome.INSUFFICIENT_INFORMATION: "not yet assessable - more information is needed",
}

FACT_LABELS = {
    "age": "age",
    "gross_annual_income": "gross annual income",
    "monthly_income": "gross monthly income",
    "employment_status": "employment status",
    "employment_months": "months in current employment",
    "credit_score": "internal credit score",
    "credit_history_months": "months of credit history",
    "requested_amount": "requested amount",
    "loan_term_months": "assessment term (months)",
    "estimated_monthly_payment": "indicative monthly repayment",
    "existing_monthly_debt": "existing monthly commitments",
    "total_monthly_obligations": "total monthly commitments",
    "dti_ratio": "debt-to-income ratio",
    "stressed_dti_ratio": "stressed debt-to-income ratio",
    "income_multiple_used": "amount as a multiple of income",
    "ltv_ratio": "loan to value",
    "liquid_savings": "liquid savings",
    "max_eligible_amount": "indicative maximum capacity",
}


def _format_value(key: str, value: Any) -> str:
    if value is None:
        return "not provided"
    if key.endswith("_ratio") and isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def format_facts(facts: Mapping[str, Any]) -> str:
    lines: List[str] = []
    for key, label in FACT_LABELS.items():
        if key in facts:
            lines.append(f"- {label}: {_format_value(key, facts[key])}")
    return "\n".join(lines)


def format_rules(decision: EligibilityDecision, include_passes: bool = False) -> str:
    lines: List[str] = []
    for result in decision.rule_results:
        if result.status == RuleStatus.NOT_APPLICABLE:
            continue
        if not include_passes and result.status == RuleStatus.PASS:
            continue
        lines.append(
            f"- {result.rule_id} ({result.policy_reference.section}) "
            f"[{result.severity.value}] {result.status.value}: {result.detail}"
        )
    return "\n".join(lines) or "- (no criteria in this category)"


def format_citations(citations: Sequence[Citation]) -> str:
    lines: List[str] = []
    for citation in citations:
        rules = f" supports: {', '.join(citation.rule_ids)}" if citation.rule_ids else ""
        lines.append(
            f"{citation.marker} {citation.document_id} {citation.section} "
            f"(policy v{citation.policy_version}, {citation.document_title}){rules}\n"
            f'    "{citation.quote}"'
        )
    return "\n".join(lines) or "(no clauses retrieved)"


def build_explanation_prompt(
    decision: EligibilityDecision,
    citations: Sequence[Citation],
    question: Optional[str] = None,
    history: Optional[Iterable[Mapping[str, str]]] = None,
) -> str:
    """The user-turn content sent to Claude."""

    history_block = ""
    if history:
        turns = [
            f"{str(turn.get('role', 'user')).upper()}: {turn.get('content', '')}"
            for turn in history
        ]
        if turns:
            history_block = (
                "\n<conversation_so_far>\n"
                + "\n".join(turns[-6:])
                + "\n</conversation_so_far>\n"
            )

    window = (
        f"in force from {decision.policy_effective_from} to {decision.policy_effective_to}"
        if decision.policy_effective_to
        else f"in force from {decision.policy_effective_from}, still current"
    )
    actions = "\n".join(f"- {action}" for action in decision.required_actions) or "- none"

    return f"""<decision>
outcome: {decision.outcome.value} ({OUTCOME_WORDING[decision.outcome]})
product: {decision.product.value}
policy_version: {decision.policy_version} ({window})
engine: {decision.engine_version}, decision hash {decision.decision_hash[:12]}
requested_amount: {_format_value("requested_amount", decision.requested_amount)}
indicative_monthly_repayment: {_format_value("estimated_monthly_payment", decision.estimated_monthly_payment)}
indicative_maximum_capacity: {_format_value("max_eligible_amount", decision.max_eligible_amount)}
</decision>

<criteria_not_met_or_unknown>
{format_rules(decision)}
</criteria_not_met_or_unknown>

<criteria_met>
{format_rules_passed(decision)}
</criteria_met>

<applicant_facts_used>
{format_facts(decision.computed_facts)}
</applicant_facts_used>

<required_actions>
{actions}
</required_actions>

<policy_clauses>
{format_citations(citations)}
</policy_clauses>
{history_block}
<applicant_question>
{question or "Am I eligible for this product, and why?"}
</applicant_question>

Write the explanation now. The outcome is fixed; explain it, cite the clauses, and do not re-decide."""


def format_rules_passed(decision: EligibilityDecision) -> str:
    lines = [
        f"- {result.rule_id} ({result.policy_reference.section}) PASS: {result.detail}"
        for result in decision.rule_results
        if result.status == RuleStatus.PASS
    ]
    return "\n".join(lines) or "- (none)"


# ---------------------------------------------------------------------------
# Deterministic fallback explanation
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "This is an indicative pre-qualification produced by an educational demonstration "
    "using synthetic DemoBank policies. It is not a credit decision and not financial advice."
)


def render_deterministic_explanation(
    decision: EligibilityDecision, citations: Sequence[Citation]
) -> str:
    """Template explanation used when Claude is unavailable, and as the guardrail
    fallback when a generated explanation contradicts the decision."""

    marker_for: Dict[str, str] = {}
    for citation in citations:
        for rule_id in citation.rule_ids:
            marker_for.setdefault(rule_id, citation.marker)

    def marker(rule_id: str) -> str:
        return f" {marker_for[rule_id]}" if rule_id in marker_for else ""

    version_line = (
        f"policy version {decision.policy_version}, in force from "
        f"{decision.policy_effective_from}"
        + (
            f" to {decision.policy_effective_to}"
            if decision.policy_effective_to
            else " and still current"
        )
    )

    lines: List[str] = []
    if decision.outcome == EligibilityOutcome.ELIGIBLE:
        lines.append(
            f"Based on {version_line}, you are pre-qualified for the "
            f"{decision.product.value.replace('_', ' ').lower()} on the information provided."
        )
    elif decision.outcome == EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS:
        lines.append(
            f"Based on {version_line}, you are pre-qualified for the "
            f"{decision.product.value.replace('_', ' ').lower()} subject to conditions: "
            "every mandatory criterion is met, but one or more supporting criteria "
            "need verification or review."
        )
    elif decision.outcome == EligibilityOutcome.NOT_ELIGIBLE:
        lines.append(
            f"Based on {version_line}, this enquiry cannot be pre-qualified because at "
            "least one mandatory criterion is not met."
        )
    else:
        lines.append(
            f"Based on {version_line}, the assessment cannot be completed yet because "
            "information required by a mandatory criterion is missing. This is not a decline."
        )

    failing = [
        result
        for result in decision.rule_results
        if result.status in {RuleStatus.FAIL, RuleStatus.UNKNOWN}
    ]
    if failing:
        lines.append("")
        lines.append("Criteria that drove this result:")
        for result in failing:
            lines.append(
                f"- {result.rule_id} ({result.policy_reference.section}): "
                f"{result.detail}.{marker(result.rule_id)}"
            )
    else:
        lines.append("")
        lines.append("Every criterion assessed was satisfied, including:")
        for result in decision.rule_results[:4]:
            lines.append(
                f"- {result.rule_id} ({result.policy_reference.section}): "
                f"{result.detail}.{marker(result.rule_id)}"
            )

    if decision.estimated_monthly_payment is not None:
        lines.append("")
        lines.append(
            "Indicative monthly repayment on the amount requested: "
            f"{decision.estimated_monthly_payment:,.2f}"
            + (
                f". Indicative maximum capacity under this policy version: "
                f"{decision.max_eligible_amount:,.0f}."
                if decision.max_eligible_amount is not None
                else "."
            )
        )

    if decision.required_actions:
        lines.append("")
        lines.append("What would change or confirm this result:")
        for action in decision.required_actions[:6]:
            lines.append(f"- {action}")

    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
