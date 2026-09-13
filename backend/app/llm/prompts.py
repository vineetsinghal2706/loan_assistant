from typing import Optional

SYSTEM_TEMPLATE = """You are the Loan Eligibility Assistant for a bank's personal loan desk.
Answer the applicant's question using ONLY the policy excerpts and the eligibility
decision provided below. Every claim must cite the rule id or policy section it
comes from, in the form [RULE_ID] or [Policy section]. If the excerpts do not
contain enough information to answer confidently, say so explicitly instead of
guessing.

Current rule version in force: {rule_version}

Eligibility decision (computed deterministically from the applicant profile):
outcome: {outcome}
reasons:
{reasons}

Retrieved policy excerpts:
{excerpts}
"""


def _format_excerpts(chunks: list[dict]) -> str:
    if not chunks:
        return "No relevant policy excerpts were retrieved."
    return "\n\n".join(
        f"[{c['section']} | doc={c['doc_id']} v={c['version']} | retrieved via {c['source_type']}]\n{c['excerpt']}"
        for c in chunks
    )


def build_system_prompt(rule_version: str, decision: Optional[object], chunks: list[dict]) -> str:
    reasons = "\n".join(
        f"- {r}"
        for r in (decision.reasons if decision else ["No applicant profile supplied; answer generally from policy."])
    )
    return SYSTEM_TEMPLATE.format(
        rule_version=rule_version,
        outcome=(decision.outcome if decision else "n/a"),
        reasons=reasons,
        excerpts=_format_excerpts(chunks),
    )


def render_stub_answer(question: str, rule_version: str, decision: Optional[object], chunks: list[dict]) -> str:
    """Deterministic offline answer used when LLM_PROVIDER=stub (the default
    for tests and CI). It mirrors what a grounded LLM answer is instructed to
    contain - the decision outcome, the rule ids behind it, and the retrieved
    policy citations - so the regression gate and promptfoo assertions can
    run end-to-end without calling an external API.
    """
    lines: list[str] = []
    if decision is not None:
        lines.append(
            f"Based on policy version {rule_version}, your application is currently: "
            f"{decision.outcome.upper()}."
        )
        for reason in decision.reasons:
            lines.append(f"- {reason}")
    else:
        lines.append(f'Here is what policy version {rule_version} says about your question: "{question}"')

    if chunks:
        lines.append("Relevant policy references:")
        for c in chunks:
            lines.append(
                f"- [{c['section']}] ({c['doc_id']} {c['version']}, {c['source_type']} match): "
                f"{c['excerpt'][:160]}..."
            )
    else:
        lines.append("No matching policy excerpt was found for this question.")

    return "\n".join(lines)
