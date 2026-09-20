#!/usr/bin/env python
"""Ingest the synthetic DemoBank policy corpus into the vector store.

Usage:
    python scripts/ingest_policies.py            # add or update
    python scripts/ingest_policies.py --rebuild  # drop the collection first
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.rag.ingest import PolicyIngestor  # noqa: E402
from app.rag.retriever import PolicyRetriever  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="Reset the collection first")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    settings = get_settings()
    settings.ensure_dirs()

    ingestor = PolicyIngestor()
    stats = ingestor.ingest(rebuild=args.rebuild)
    print(json.dumps(stats.as_dict(), indent=2))

    retriever = PolicyRetriever(store=ingestor.store, embedder=ingestor.embedder)
    print("\nPer-version chunk counts:")
    for version_dir in sorted(settings.policies_dir.iterdir()):
        if not version_dir.is_dir():
            continue
        version = version_dir.name.lstrip("vV")
        print(f"  policy v{version}: {retriever.count(policy_version=version)} chunks")

    sample = retriever.search(
        "maximum debt to income ratio", policy_version="2.0", top_k=3
    )
    print("\nSmoke test - 'maximum debt to income ratio' under policy v2.0:")
    for chunk in sample:
        print(f"  {chunk.document_id} {chunk.section} (score {chunk.score:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
