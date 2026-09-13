#!/usr/bin/env python3
"""Scaffolds backend/scripts (index builder, regression-eval gate) and
backend/tests (unit + API tests)."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

FILES["backend/scripts/build_index.py"] = r'''"""Builds (or rebuilds) the hybrid retrieval index from the policy documents
under backend/data/policies and persists it to backend/data/index. Run this
whenever a policy document is added or changed:

    python scripts/build_index.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.rag.hybrid_retriever import HybridRetriever  # noqa: E402


def main():
    retriever = HybridRetriever(settings.embedding_model, settings.index_dir)
    retriever.build(settings.policy_dir)
    print(f"Indexed {len(retriever.chunks)} policy chunks into {settings.index_dir}")


if __name__ == "__main__":
    main()
'''

FILES["backend/scripts/run_regression_eval.py"] = r'''"""Regression gate for the Loan Eligibility Assistant.

Replays every labelled question in backend/data/eval/labelled_eligibility_set.jsonl
against a running instance of the API and checks two things per case:

  1. eligibility-answer accuracy - does the deterministic decision returned by
     /v1/chat match the expected outcome?
  2. faithfulness - does the natural-language answer actually contain the
     rule ids / keywords it is supposed to cite, i.e. is it grounded in the
     retrieved policy text and the computed decision rather than hallucinated?

Exits non-zero (failing the CI job and therefore blocking the merge) if
either metric falls below its threshold. This is the second, independent
layer of the CI/CD gate alongside the promptfoo assertions in
promptfoo/promptfooconfig.yaml.

Usage:
    python scripts/run_regression_eval.py --base-url http://localhost:8000
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

DEFAULT_EVAL_SET = Path(__file__).resolve().parent.parent / "data" / "eval" / "labelled_eligibility_set.jsonl"


def load_eval_set(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run(base_url: str, eval_set_path: Path):
    cases = load_eval_set(eval_set_path)
    correct = 0
    faithful = 0
    latencies = []
    failures = []

    with httpx.Client(timeout=60.0) as client:
        for case in cases:
            payload = {
                "session_id": "regression-eval",
                "message": case["question"],
                "applicant": case.get("applicant"),
            }
            resp = client.post(f"{base_url}/v1/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            latencies.append(data["latency_ms"])

            actual_outcome = (data.get("decision") or {}).get("outcome")
            expected_outcome = case.get("expected_outcome")
            is_correct = (expected_outcome is None) or (actual_outcome == expected_outcome)
            correct += int(is_correct)

            answer = data["answer"]
            expected_rule_ids = case.get("expected_rule_ids", [])
            expected_keywords = case.get("expected_keywords", [])
            is_faithful = all(rid in answer for rid in expected_rule_ids) and all(
                kw.lower() in answer.lower() for kw in expected_keywords
            )
            faithful += int(is_faithful)

            if not (is_correct and is_faithful):
                failures.append(
                    {
                        "question": case["question"],
                        "expected_outcome": expected_outcome,
                        "actual_outcome": actual_outcome,
                        "expected_rule_ids": expected_rule_ids,
                        "expected_keywords": expected_keywords,
                        "answer": answer,
                    }
                )

    n = len(cases)
    accuracy = correct / n if n else 0.0
    faithfulness = faithful / n if n else 0.0
    p95_latency = sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else 0.0

    print(f"Eligibility-answer accuracy : {accuracy:.2%} ({correct}/{n})")
    print(f"Faithfulness (grounding)     : {faithfulness:.2%} ({faithful}/{n})")
    print(f"p95 latency                 : {p95_latency:.1f} ms")

    if failures:
        print("\nFailing cases:")
        for f in failures:
            print(f"  - Q: {f['question']}")
            print(
                f"    expected={f['expected_outcome']} actual={f['actual_outcome']} "
                f"expected_rules={f['expected_rule_ids']} expected_keywords={f['expected_keywords']}"
            )

    return accuracy, faithfulness, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET))
    parser.add_argument("--fail-under-accuracy", type=float, default=0.9)
    parser.add_argument("--fail-under-faithfulness", type=float, default=0.8)
    args = parser.parse_args()

    accuracy, faithfulness, _failures = run(args.base_url, Path(args.eval_set))

    if accuracy < args.fail_under_accuracy or faithfulness < args.fail_under_faithfulness:
        print(
            f"\nREGRESSION GATE FAILED: accuracy={accuracy:.2%} "
            f"(threshold {args.fail_under_accuracy:.0%}), "
            f"faithfulness={faithfulness:.2%} (threshold {args.fail_under_faithfulness:.0%})"
        )
        sys.exit(1)

    print("\nRegression gate passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
'''

FILES["backend/tests/test_rules_engine.py"] = r'''from pathlib import Path

from app.eligibility.rules_engine import RulesEngine
from app.models import ApplicantProfile

RULES_DIR = Path(__file__).resolve().parent.parent / "data" / "eligibility_rules"


def _engine():
    return RulesEngine(RULES_DIR)


def test_available_versions():
    engine = _engine()
    assert set(engine.available_versions()) == {"v1", "v2"}


def test_eligible_under_v2():
    engine = _engine()
    applicant = ApplicantProfile(
        monthly_income=40000,
        monthly_debt=5000,
        credit_score=720,
        age=30,
        employment_type="salaried",
        requested_amount=200000,
        loan_tenure_months=36,
    )
    decision = engine.evaluate(applicant, "v2")
    assert decision.outcome == "eligible"


def test_income_regression_v1_vs_v2():
    """An applicant who qualified under the v1 income floor (Rs 20k) but
    falls below the v2 floor (Rs 25k) must be declined under v2 - this
    proves the policy *update* document is actually enforced, not just the
    original policy."""
    engine = _engine()
    applicant = ApplicantProfile(
        monthly_income=22000,
        monthly_debt=2000,
        credit_score=700,
        age=30,
        employment_type="salaried",
        requested_amount=100000,
        loan_tenure_months=24,
    )
    assert engine.evaluate(applicant, "v1").outcome == "eligible"

    decision_v2 = engine.evaluate(applicant, "v2")
    assert decision_v2.outcome == "not_eligible"
    assert "R-INC-01" in decision_v2.rule_ids
    assert any("R-INC-01" in reason for reason in decision_v2.reasons)


def test_needs_review_borderline_credit_score():
    engine = _engine()
    applicant = ApplicantProfile(
        monthly_income=30000,
        monthly_debt=3000,
        credit_score=640,
        age=25,
        employment_type="salaried",
        requested_amount=100000,
        loan_tenure_months=36,
    )
    decision = engine.evaluate(applicant, "v2")
    assert decision.outcome == "needs_review"
    assert "R-CS-01" in decision.rule_ids


def test_not_eligible_when_unemployed():
    engine = _engine()
    applicant = ApplicantProfile(
        monthly_income=30000,
        monthly_debt=2000,
        credit_score=700,
        age=30,
        employment_type="unemployed",
        requested_amount=50000,
        loan_tenure_months=12,
    )
    decision = engine.evaluate(applicant, "v2")
    assert decision.outcome == "not_eligible"
    assert "R-EMP-01" in decision.rule_ids
'''

FILES["backend/tests/test_hybrid_retriever.py"] = r'''"""Requires outbound network access on first run to download the
sentence-transformers embedding model (cached afterwards)."""
import tempfile
from pathlib import Path

from app.rag.hybrid_retriever import HybridRetriever

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"


def test_hybrid_retrieval_prefers_updated_policy():
    with tempfile.TemporaryDirectory() as tmp:
        retriever = HybridRetriever("sentence-transformers/all-MiniLM-L6-v2", Path(tmp))
        retriever.build(POLICY_DIR)

        results = retriever.search("What is the minimum income requirement for a personal loan?", top_k=3)
        assert len(results) > 0

        versions_present = {r["version"] for r in results}
        assert "v2" in versions_present

        best = max(results, key=lambda r: r["score"])
        assert best["version"] == "v2"
'''

FILES["backend/tests/test_api.py"] = r'''import os

os.environ.setdefault("LLM_PROVIDER", "stub")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_chat_eligible_applicant(client):
    payload = {
        "session_id": "test",
        "message": "Am I eligible for a personal loan given my profile?",
        "applicant": {
            "monthly_income": 40000,
            "monthly_debt": 5000,
            "credit_score": 720,
            "age": 30,
            "employment_type": "salaried",
            "requested_amount": 200000,
            "loan_tenure_months": 36,
        },
    }
    resp = client.post("/v1/chat", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"]["outcome"] == "eligible"
    assert "ELIGIBLE" in data["answer"]


def test_chat_declines_low_income(client):
    payload = {
        "session_id": "test",
        "message": "Am I eligible for a personal loan given my profile?",
        "applicant": {
            "monthly_income": 15000,
            "monthly_debt": 2000,
            "credit_score": 700,
            "age": 28,
            "employment_type": "salaried",
            "requested_amount": 100000,
            "loan_tenure_months": 24,
        },
    }
    resp = client.post("/v1/chat", json=payload)
    data = resp.json()
    assert data["decision"]["outcome"] == "not_eligible"
    assert "R-INC-01" in data["answer"]
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
