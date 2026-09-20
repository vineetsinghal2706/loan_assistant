"""Post-generation guardrails.

The deterministic decision is streamed to the client *before* any generated
token, so the user always sees the authoritative outcome first. The generated
explanation is then checked for:

* contradiction of the recorded outcome,
* commitment language ("approved", "guaranteed"),
* missing or invented citation markers,
* reference to a policy version other than the one applied.

Violations are counted in Prometheus, stored in the audit record, and surfaced
to the client as a ``guardrail`` event with the deterministic explanation as a
correction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence

from app.schemas import Citation, EligibilityDecision, EligibilityOutcome

COMMITMENT_PATTERNS = [
    (r"\bguarantee(?:d|s)?\b", "commitment language: 'guarantee'"),
    (r"\bwe will lend\b", "commitment language: 'we will lend'"),
    (r"\byou(?:'re| are) approved\b", "commitment language: 'approved'"),
    (r"\bloan (?:is )?approved\b", "commitment language: 'approved'"),
    (r"\bfinal (?:credit )?decision\b", "misrepresents an indicative pre-qualification"),
]

ADVICE_PATTERNS = [
    (r"\byou should invest\b", "investment advice"),
    (r"\bi recommend (?:that )?you (?:buy|invest|refinance)\b", "product recommendation"),
]

# "declined" and friends are matched as verdicts. The bare noun "decline" is
# deliberately NOT matched, because the correct wording for
# INSUFFICIENT_INFORMATION is "this is not a decline".
VERDICT_DECLINE = r"\b(?:declined|declining|rejected|refused)\b"

CONTRADICTION_PATTERNS = {
    EligibilityOutcome.ELIGIBLE: [
        (r"\bnot\s+(?:pre-?qualified|eligible)\b", "contradicts ELIGIBLE"),
        (VERDICT_DECLINE, "contradicts ELIGIBLE"),
        (r"\bunfortunately\b", "tone contradicts ELIGIBLE"),
    ],
    EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS: [
        (VERDICT_DECLINE, "contradicts ELIGIBLE_WITH_CONDITIONS"),
        (
            r"\bno (?:further )?conditions\b",
            "contradicts ELIGIBLE_WITH_CONDITIONS (conditions exist)",
        ),
    ],
    EligibilityOutcome.NOT_ELIGIBLE: [
        (r"\byou (?:are|'re) (?:pre-?qualified|eligible)\b", "contradicts NOT_ELIGIBLE"),
        (r"\bcongratulations\b", "contradicts NOT_ELIGIBLE"),
        (r"\bmeets all (?:the )?(?:criteria|requirements)\b", "contradicts NOT_ELIGIBLE"),
    ],
    EligibilityOutcome.INSUFFICIENT_INFORMATION: [
        (
            VERDICT_DECLINE,
            "insufficient information must never be presented as a decline",
        ),
        (
            r"\bnot eligible\b",
            "insufficient information must never be presented as a decline",
        ),
        (
            r"\byou (?:are|'re) (?:pre-?qualified|eligible)\b",
            "contradicts INSUFFICIENT_INFORMATION",
        ),
    ],
}

MARKER_RE = re.compile(r"\[(\d{1,2})\]")
VERSION_RE = re.compile(r"\bv(?:ersion)?\s*([0-9]+\.[0-9]+)\b", re.IGNORECASE)


@dataclass
class GuardrailReport:
    ok: bool = True
    violations: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add(self, violation: str) -> None:
        if violation not in self.violations:
            self.violations.append(violation)
        self.ok = False


def validate_explanation(
    text: str,
    decision: EligibilityDecision,
    citations: Sequence[Citation] = (),
    require_citations: bool = True,
) -> GuardrailReport:
    report = GuardrailReport()
    body = (text or "").strip()

    if not body:
        report.add("empty explanation")
        return report

    lowered = body.lower()

    for pattern, label in CONTRADICTION_PATTERNS.get(decision.outcome, []):
        if re.search(pattern, lowered):
            report.add(label)

    for pattern, label in COMMITMENT_PATTERNS + ADVICE_PATTERNS:
        if re.search(pattern, lowered):
            report.add(label)

    markers_used = {int(value) for value in MARKER_RE.findall(body)}
    available = {int(citation.marker.strip("[]")) for citation in citations}

    if citations and require_citations and not markers_used:
        report.add("no citation markers were used")
    invented = markers_used - available
    if invented:
        report.add(
            "invented citation marker(s): " + ", ".join(f"[{value}]" for value in sorted(invented))
        )
    unused = available - markers_used
    if unused and citations:
        report.notes.append(
            "unused clauses: " + ", ".join(f"[{value}]" for value in sorted(unused))
        )

    for found in VERSION_RE.findall(body):
        if found != decision.policy_version:
            report.add(
                f"references policy version {found} but the decision applied "
                f"version {decision.policy_version}"
            )

    return report
