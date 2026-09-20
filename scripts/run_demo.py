#!/usr/bin/env python
"""End-to-end demonstration without starting the API server.

Runs the full pipeline for the bundled synthetic applicants:

    document -> extraction -> rules -> RAG -> explanation -> audit

Usage:
    python scripts/run_demo.py
    python scripts/run_demo.py --applicant applicant_b_borderline_credit_score.md
    python scripts/run_demo.py --compare
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.extraction.applicant_extractor import extract_from_path  # noqa: E402
from app.observability.middleware import new_trace_id  # noqa: E402
from app.pipeline import EligibilityPipeline  # noqa: E402
from app.rag.ingest import PolicyIngestor  # noqa: E402
from app.rules.engine import RuleEngine  # noqa: E402
from app.schemas import LoanProduct  # noqa: E402

PRODUCT_BY_FILE = {
    "applicant_d_mortgage_first_time_buyer.md": LoanProduct.MORTGAGE,
}


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


async def run_one(path: Path, policy_version: str | None, compare: bool) -> None:
    banner(f"APPLICANT DOCUMENT: {path.name}")
    extraction = extract_from_path(path)
    print(f"parser={extraction.parser} pages={extraction.page_count}")
    for extracted in extraction.fields:
        page = extracted.provenance.page if extracted.provenance else "-"
        print(f"  {extracted.field_name:<26} = {extracted.value!r:<22} (page {page})")
    if extraction.missing_fields:
        print(f"  missing: {', '.join(extraction.missing_fields)}")

    product = PRODUCT_BY_FILE.get(path.name, LoanProduct.PERSONAL_LOAN)
    pipeline = EligibilityPipeline()

    banner(f"DETERMINISTIC DECISION ({product.value})")
    result = await pipeline.run(
        applicant=extraction.applicant,
        product=product,
        trace_id=new_trace_id(),
        policy_version=policy_version,
        question="Am I eligible, and why?",
        explain=True,
    )
    decision = result.decision
    print(f"outcome            : {decision.outcome.value}")
    print(f"policy version     : {decision.policy_version}")
    print(f"decision hash      : {decision.decision_hash}")
    print(f"monthly repayment  : {decision.estimated_monthly_payment}")
    print(f"indicative capacity: {decision.max_eligible_amount}")
    print("criteria:")
    for rule in decision.rule_results:
        print(f"  [{rule.status.value:<14}] {rule.rule_id:<28} {rule.detail}")

    banner("CITATIONS (retrieved from the applied policy version)")
    for citation in result.citations:
        print(f"{citation.marker} {citation.document_id} {citation.section}: {citation.quote[:160]}")

    banner(f"EXPLANATION (mode: {result.explanation_mode.value})")
    print(result.explanation)
    if result.guardrail_violations:
        print("\nGUARDRAIL VIOLATIONS:", result.guardrail_violations)
    print(f"\naudit record id: {result.audit_id}")

    if compare:
        banner("POLICY VERSION COMPARISON")
        comparison = RuleEngine().compare_versions(extraction.applicant, product=product)
        for entry in comparison.entries:
            print(
                f"  v{entry.policy_version}: {entry.outcome.value:<28} "
                f"failed_hard={entry.failed_hard_rules or '-'}"
            )
        for difference in comparison.differences:
            print(f"  * {difference}")


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    PolicyIngestor().ensure_index()

    directory = settings.applicants_dir
    if args.applicant:
        paths = [directory / args.applicant]
    else:
        paths = sorted(path for path in directory.iterdir() if path.is_file())

    for path in paths:
        if not path.exists():
            print(f"No such applicant document: {path}")
            return 1
        await run_one(path, args.policy_version, args.compare)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--applicant", help="File name inside data/applicants")
    parser.add_argument("--policy-version", dest="policy_version", default=None)
    parser.add_argument("--compare", action="store_true", help="Also compare policy versions")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
