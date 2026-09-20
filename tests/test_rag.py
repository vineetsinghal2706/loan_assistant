"""RAG: chunking by clause, deterministic embeddings, and version isolation."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.rag.chunker import chunk_markdown, parse_front_matter
from app.rag.embeddings import HashingEmbedder
from app.rag.retriever import PolicyRetriever
from app.rag.store import InMemoryVectorStore
from app.rules.engine import RuleEngine


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def _policy_text(version: str, filename: str) -> str:
    path = get_settings().policies_dir / f"v{version}" / filename
    return path.read_text(encoding="utf-8")


def test_front_matter_is_parsed():
    front, body = parse_front_matter(_policy_text("2.0", "01_personal_loan_eligibility.md"))
    assert front["document_id"] == "DB-PL"
    assert str(front["policy_version"]) == "2.0"
    assert body.lstrip().startswith("#")


def test_chunks_are_keyed_by_clause():
    chunks = chunk_markdown(
        _policy_text("2.0", "01_personal_loan_eligibility.md"), source_path="test.md"
    )
    sections = {chunk.section for chunk in chunks}
    assert {"PL-2.1", "PL-3.2", "PL-4.2", "PL-5.2"} <= sections
    assert all(chunk.policy_version == "2.0" for chunk in chunks)
    assert all(chunk.document_id == "DB-PL" for chunk in chunks)

    tenure = next(chunk for chunk in chunks if chunk.section == "PL-3.2")
    assert "12 consecutive months" in tenure.text
    assert tenure.chunk_id.startswith("v2.0|DB-PL|PL-3.2|")
    # the clause header is prepended so a retrieved chunk is self-describing
    assert "PL-3.2" in tenure.text.splitlines()[0]


def test_credit_standards_differ_between_versions():
    v1 = chunk_markdown(
        _policy_text("1.0", "04_credit_and_affordability_standards.md"), source_path="v1.md"
    )
    v2 = chunk_markdown(
        _policy_text("2.0", "04_credit_and_affordability_standards.md"), source_path="v2.md"
    )
    v1_score = next(chunk for chunk in v1 if chunk.section == "CR-2.1")
    v2_score = next(chunk for chunk in v2 if chunk.section == "CR-2.1")
    assert "640" in v1_score.text
    assert "660" in v2_score.text
    assert "CR-3.3" not in {chunk.section for chunk in v1}
    assert "CR-3.3" in {chunk.section for chunk in v2}


# ---------------------------------------------------------------------------
# Embeddings and store
# ---------------------------------------------------------------------------
def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder()
    first = embedder.embed_query("maximum debt to income ratio")
    second = embedder.embed_query("maximum debt to income ratio")
    assert first == second
    assert len(first) == embedder.dimension
    norm = sum(value * value for value in first) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)
    assert embedder.embed_query("something completely different") != first


def test_in_memory_store_filters_on_metadata():
    embedder = HashingEmbedder()
    chunks = chunk_markdown(
        _policy_text("1.0", "04_credit_and_affordability_standards.md"), source_path="v1.md"
    ) + chunk_markdown(
        _policy_text("2.0", "04_credit_and_affordability_standards.md"), source_path="v2.md"
    )
    store = InMemoryVectorStore()
    store.upsert(chunks, embedder.embed_documents([chunk.text for chunk in chunks]))

    assert store.count() == len(chunks)
    assert store.count({"policy_version": "1.0"}) < store.count()

    matches = store.query(
        embedder.embed_query("minimum credit score"),
        top_k=3,
        where={"policy_version": "1.0"},
    )
    assert matches
    assert all(match.metadata["policy_version"] == "1.0" for match in matches)


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------
def test_index_holds_both_versions(indexed_retriever: PolicyRetriever):
    assert indexed_retriever.count() > 0
    assert indexed_retriever.count(policy_version="1.0") > 0
    assert indexed_retriever.count(policy_version="2.0") > 0


def test_search_never_crosses_policy_versions(indexed_retriever: PolicyRetriever):
    for version in ("1.0", "2.0"):
        chunks = indexed_retriever.search(
            "minimum credit score for a personal loan", policy_version=version, top_k=5
        )
        assert chunks
        assert all(chunk.policy_version == version for chunk in chunks)


def test_clause_lookup_returns_the_requested_clause(indexed_retriever: PolicyRetriever):
    clause = indexed_retriever.clause("2.0", "DB-CR", "CR-2.1")
    assert clause is not None
    assert clause.section == "CR-2.1"
    assert clause.policy_version == "2.0"
    assert "660" in clause.text

    older = indexed_retriever.clause("1.0", "DB-CR", "CR-2.1")
    assert older is not None
    assert "640" in older.text
    assert older.chunk_id != clause.chunk_id


def test_citations_match_the_applied_version(
    indexed_retriever: PolicyRetriever, engine: RuleEngine, clean_applicant
):
    applicant = clean_applicant.model_copy(update={"credit_score": 645})
    decision = engine.evaluate(applicant, policy_version="2.0")
    citations, chunks = indexed_retriever.citations_for_decision(
        decision, question="why is my score not enough?"
    )

    assert citations
    assert chunks > 0
    assert all(citation.policy_version == "2.0" for citation in citations)
    assert [citation.marker for citation in citations] == [
        f"[{index + 1}]" for index in range(len(citations))
    ]
    score_citation = next(
        citation for citation in citations if "CR-2.1-CREDIT-SCORE" in citation.rule_ids
    )
    assert score_citation.section == "CR-2.1"
    assert "660" in score_citation.quote
    assert score_citation.quote.strip() != ""


def test_citations_for_v1_decision_quote_v1_thresholds(
    indexed_retriever: PolicyRetriever, engine: RuleEngine, clean_applicant
):
    applicant = clean_applicant.model_copy(update={"credit_score": 600})
    decision = engine.evaluate(applicant, policy_version="1.0")
    citations, _ = indexed_retriever.citations_for_decision(decision)
    score_citation = next(
        citation for citation in citations if "CR-2.1-CREDIT-SCORE" in citation.rule_ids
    )
    assert score_citation.policy_version == "1.0"
    assert "640" in score_citation.quote
