"""Promptfoo custom provider: evaluates the *whole pipeline*, not a bare prompt.

Promptfoo calls :func:`call_api` for each test case. The provider

1. loads the applicant from the golden regression file (or from inline vars),
2. runs the deterministic rule engine for the requested policy version,
3. retrieves citations from that same policy version,
4. asks the explainer (Claude when ANTHROPIC_API_KEY is set, otherwise the
   deterministic template) to explain the decision,
5. returns the explanation as the output, plus the decision in ``metadata`` so
   assertions can compare the text against the authoritative outcome.

Run with:  npx promptfoo@latest eval -c promptfoo/promptfooconfig.yaml
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.claude_client import ClaudeExplainer  # noqa: E402
from app.rag.ingest import PolicyIngestor  # noqa: E402
from app.rag.retriever import get_retriever  # noqa: E402
from app.rules.engine import RuleEngine, summarise_decision  # noqa: E402
from app.schemas import ApplicantProfile, LoanProduct  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "golden" / "regression_cases.json"

_cases: Optional[Dict[str, Dict[str, Any]]] = None
_ready = False


def _golden_cases() -> Dict[str, Dict[str, Any]]:
    global _cases
    if _cases is None:
        with GOLDEN_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        _cases = {case["id"]: case for case in payload["cases"]}
    return _cases


def _ensure_index() -> None:
    global _ready
    if not _ready:
        PolicyIngestor().ensure_index()
        _ready = True


def _applicant_from_vars(variables: Dict[str, Any]) -> tuple:
    case_id = variables.get("case_id")
    if case_id:
        case = _golden_cases().get(case_id)
        if case is None:
            raise ValueError(f"Unknown golden case id '{case_id}'")
        applicant = ApplicantProfile(**case["applicant"])
        product = LoanProduct(variables.get("product") or case["product"])
        return applicant, product
    inline = variables.get("applicant")
    if isinstance(inline, str):
        inline = json.loads(inline)
    if not inline:
        raise ValueError("A promptfoo test must supply either 'case_id' or 'applicant'")
    return (
        ApplicantProfile(**inline),
        LoanProduct(variables.get("product") or "PERSONAL_LOAN"),
    )


def call_api(prompt: str, options: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    variables: Dict[str, Any] = (context or {}).get("vars", {}) or {}
    _ensure_index()

    try:
        applicant, product = _applicant_from_vars(variables)
    except Exception as exc:
        return {"error": str(exc)}

    policy_version = str(variables.get("policy_version") or "2.0")
    question = variables.get("question") or prompt or "Am I eligible, and why?"

    engine = RuleEngine()
    decision = engine.evaluate(
        applicant, product=product, policy_version=policy_version
    )
    citations, chunk_count = get_retriever().citations_for_decision(
        decision, question=question
    )

    explainer = ClaudeExplainer()
    record = asyncio.run(explainer.explain(decision, citations, question=question))

    usage: Dict[str, Any] = {}
    if record.input_tokens or record.output_tokens:
        usage = {
            "prompt": record.input_tokens or 0,
            "completion": record.output_tokens or 0,
            "total": (record.input_tokens or 0) + (record.output_tokens or 0),
        }

    return {
        "output": record.text,
        "tokenUsage": usage or None,
        "metadata": {
            "case_id": variables.get("case_id"),
            "policy_version": decision.policy_version,
            "outcome": decision.outcome.value,
            "decision_hash": decision.decision_hash,
            "explanation_mode": record.mode.value,
            "llm_model": record.model,
            "retrieved_chunks": chunk_count,
            "citations": [
                {
                    "marker": citation.marker,
                    "document_id": citation.document_id,
                    "section": citation.section,
                    "policy_version": citation.policy_version,
                    "rule_ids": citation.rule_ids,
                }
                for citation in citations
            ],
            "decision": summarise_decision(decision),
        },
    }
