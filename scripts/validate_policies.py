#!/usr/bin/env python
"""Static integrity check between the rule packs and the policy corpus.

Run in CI before the tests. It answers three questions that a unit test cannot:

1. Does every criterion cite a clause that actually exists, in its own policy
   version?
2. Is every document a rule cites declared by that policy version?
3. Does the number a criterion enforces actually appear in the clause it cites?
   (Guards against the rule pack drifting away from the written policy.)

Usage:
    python scripts/validate_policies.py
    python scripts/validate_policies.py --strict   # warnings become failures
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.rag.chunker import chunk_markdown  # noqa: E402
from app.rules.registry import PolicyRegistry  # noqa: E402

Clause = Tuple[str, str]  # (document_id, section)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def load_corpus() -> Dict[str, Dict[Clause, str]]:
    """version -> {(document_id, section): clause text}"""
    corpus: Dict[str, Dict[Clause, str]] = defaultdict(dict)
    root = get_settings().policies_dir
    for version_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        version = version_dir.name.lstrip("vV")
        for path in sorted(version_dir.glob("*.md")):
            for chunk in chunk_markdown(
                path.read_text(encoding="utf-8"), source_path=str(path), policy_version=version
            ):
                key = (chunk.document_id, chunk.section)
                existing = corpus[version].get(key, "")
                corpus[version][key] = normalise(existing + " " + chunk.text)
    return corpus


def threshold_candidates(threshold: object) -> List[str]:
    """How the enforced number might legitimately be written in prose."""
    if isinstance(threshold, bool) or threshold is None:
        return []
    if isinstance(threshold, str):
        return []
    if isinstance(threshold, int):
        if threshold == 0:
            return []
        return [f"{threshold:,}", str(threshold)]
    if isinstance(threshold, float):
        if threshold == 0:
            return []
        candidates = [str(threshold)]
        if 0 < threshold < 1:
            candidates.append(f"{int(round(threshold * 100))}%")
        else:
            candidates.append(f"{threshold:,.1f}")
            if threshold.is_integer():
                candidates.append(f"{int(threshold):,}")
        return candidates
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    corpus = load_corpus()
    registry = PolicyRegistry()

    errors: List[str] = []
    warnings: List[str] = []
    checked = 0

    print("Policy corpus:")
    for version, clauses in sorted(corpus.items()):
        documents: Set[str] = {document for document, _ in clauses}
        print(f"  v{version}: {len(clauses)} clauses across {len(documents)} documents")

    for version in registry.versions:
        pack = registry.get(version)
        clauses = corpus.get(version)
        if not clauses:
            errors.append(f"policy version {version} has a rule pack but no documents")
            continue

        declared = set(pack.documents)
        actual = {document for document, _ in clauses}
        for missing in sorted(declared - actual):
            errors.append(f"v{version}: pack declares document {missing} but no file provides it")
        for extra in sorted(actual - declared):
            warnings.append(f"v{version}: document {extra} exists but is not declared by the pack")

        for product, config in sorted(pack.products.items()):
            for rule in config.rules:
                checked += 1
                reference = dict(rule.policy_reference)
                key = (reference["document_id"], reference["section"])

                if reference.get("policy_version") != version:
                    errors.append(
                        f"v{version}/{product}/{rule.id}: cites policy version "
                        f"{reference.get('policy_version')}"
                    )
                if key not in clauses:
                    errors.append(
                        f"v{version}/{product}/{rule.id}: clause {key[0]} {key[1]} "
                        f"does not exist in policy version {version}"
                    )
                    continue

                text = clauses[key]
                for candidate in threshold_candidates(rule.threshold):
                    if candidate in text:
                        break
                else:
                    if threshold_candidates(rule.threshold):
                        warnings.append(
                            f"v{version}/{product}/{rule.id}: threshold "
                            f"{rule.threshold!r} is not written in clause "
                            f"{key[0]} {key[1]}"
                        )
                if not reference.get("quote"):
                    warnings.append(
                        f"v{version}/{product}/{rule.id}: no quote supplied for citations"
                    )

    print(f"\nChecked {checked} criteria across {len(registry.versions)} policy versions.")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR:   {error}")

    if errors or (args.strict and warnings):
        print("\nPolicy integrity check FAILED")
        return 1
    print("\nPolicy integrity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
