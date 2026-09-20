"""Policy retrieval and citation assembly.

Two retrieval modes are combined:

1. **Clause-targeted retrieval** - for every criterion the rule engine actually
   relied on, fetch that exact clause from the *same policy version* that was
   applied. This is what guarantees the citation matches the decision.
2. **Semantic retrieval** - for the applicant's free-text question, fetch
   additional context from the same policy version.

The retriever never sees the outcome logic; it only answers "what does this
version of the policy say?".
"""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.config import get_settings
from app.rag.embeddings import Embedder, get_embedder
from app.rag.store import StoredMatch, VectorStore, get_vector_store
from app.schemas import Citation, EligibilityDecision, RetrievedChunk, RuleStatus

logger = logging.getLogger(__name__)

MAX_CITATIONS = 8
QUOTE_MAX_CHARS = 420
HEADER_LINE_RE = re.compile(r"^\[[^\]]+\]\s*\([^)]*\)\s*\n?")


def _clean_quote(text: str, max_chars: int = QUOTE_MAX_CHARS) -> str:
    body = HEADER_LINE_RE.sub("", text or "").strip()
    body = re.sub(r"\s*\n\s*", " ", body)
    body = re.sub(r"\s{2,}", " ", body)
    body = body.replace("**", "")
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    cut = truncated.rfind(". ")
    if cut > max_chars * 0.5:
        return truncated[: cut + 1]
    return truncated.rstrip() + "..."


def _to_chunk(match: StoredMatch) -> RetrievedChunk:
    meta: Mapping[str, Any] = match.metadata or {}
    return RetrievedChunk(
        chunk_id=match.chunk_id,
        document_id=str(meta.get("document_id", "UNKNOWN")),
        document_title=str(meta.get("document_title", meta.get("document_id", "UNKNOWN"))),
        section=str(meta.get("section", "UNKNOWN")),
        policy_version=str(meta.get("policy_version", "unknown")),
        text=match.text,
        score=match.score,
        source_path=str(meta.get("source_path")) if meta.get("source_path") else None,
    )


class PolicyRetriever:
    def __init__(
        self,
        store: Optional[VectorStore] = None,
        embedder: Optional[Embedder] = None,
        top_k: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.store = store or get_vector_store()
        self.embedder = embedder or get_embedder()
        self.top_k = top_k or settings.retrieval_top_k
        self.last_latency_seconds: float = 0.0

    # ------------------------------------------------------------------
    @property
    def backend(self) -> str:
        return self.store.backend

    def count(self, policy_version: Optional[str] = None) -> int:
        where = {"policy_version": policy_version} if policy_version else None
        return self.store.count(where)

    def search(
        self,
        query: str,
        policy_version: Optional[str] = None,
        top_k: Optional[int] = None,
        document_id: Optional[str] = None,
        section: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        where: Dict[str, Any] = {}
        if policy_version:
            where["policy_version"] = str(policy_version)
        if document_id:
            where["document_id"] = document_id
        if section:
            where["section"] = section

        started = time.perf_counter()
        vector = self.embedder.embed_query(query or "")
        matches = self.store.query(vector, top_k=top_k or self.top_k, where=where or None)
        self.last_latency_seconds = time.perf_counter() - started
        return [_to_chunk(match) for match in matches]

    def clause(
        self, policy_version: str, document_id: str, section: str
    ) -> Optional[RetrievedChunk]:
        """Fetch one specific clause from one specific policy version."""
        results = self.search(
            query=f"{document_id} {section}",
            policy_version=policy_version,
            document_id=document_id,
            section=section,
            top_k=1,
        )
        return results[0] if results else None

    # ------------------------------------------------------------------
    def citations_for_decision(
        self,
        decision: EligibilityDecision,
        question: Optional[str] = None,
        extra_semantic: int = 2,
    ) -> Tuple[List[Citation], int]:
        """Build the citation list for a decision. Returns (citations, chunks seen)."""

        version = decision.policy_version
        rules_by_section: Dict[Tuple[str, str], List[str]] = {}
        quotes_by_section: Dict[Tuple[str, str], str] = {}
        ordered: List[Tuple[str, str]] = []

        relevant = [
            result
            for result in decision.rule_results
            if result.status in {RuleStatus.FAIL, RuleStatus.UNKNOWN}
        ] or [result for result in decision.rule_results if result.status == RuleStatus.PASS]

        for result in relevant:
            key = (result.policy_reference.document_id, result.policy_reference.section)
            if key not in rules_by_section:
                rules_by_section[key] = []
                ordered.append(key)
            rules_by_section[key].append(result.rule_id)
            if result.policy_reference.quote and key not in quotes_by_section:
                quotes_by_section[key] = result.policy_reference.quote

        citations: List[Citation] = []
        chunks_seen = 0

        for document_id, section in ordered[:MAX_CITATIONS]:
            chunk = self.clause(version, document_id, section)
            if chunk is not None:
                chunks_seen += 1
                quote = _clean_quote(chunk.text)
                title = chunk.document_title
                score = chunk.score
                source_path = chunk.source_path
            else:
                quote = quotes_by_section.get((document_id, section), "")
                title = document_id
                score = None
                source_path = None
            citations.append(
                Citation(
                    marker=f"[{len(citations) + 1}]",
                    document_id=document_id,
                    document_title=title,
                    section=section,
                    policy_version=version,
                    quote=quote or "(clause text not indexed)",
                    rule_ids=sorted(set(rules_by_section.get((document_id, section), []))),
                    score=score,
                    source_path=source_path,
                )
            )

        if question and extra_semantic > 0 and len(citations) < MAX_CITATIONS:
            existing = {(citation.document_id, citation.section) for citation in citations}
            extras = self.search(question, policy_version=version, top_k=extra_semantic + 3)
            for chunk in extras:
                key = (chunk.document_id, chunk.section)
                if key in existing or chunk.section == "PREAMBLE":
                    continue
                chunks_seen += 1
                citations.append(
                    Citation(
                        marker=f"[{len(citations) + 1}]",
                        document_id=chunk.document_id,
                        document_title=chunk.document_title,
                        section=chunk.section,
                        policy_version=chunk.policy_version,
                        quote=_clean_quote(chunk.text),
                        rule_ids=[],
                        score=chunk.score,
                        source_path=chunk.source_path,
                    )
                )
                existing.add(key)
                if len(citations) >= MAX_CITATIONS or len(
                    [c for c in citations if not c.rule_ids]
                ) >= extra_semantic:
                    break

        return citations, chunks_seen


@lru_cache(maxsize=1)
def get_retriever() -> PolicyRetriever:
    return PolicyRetriever()


def reset_retriever_cache() -> None:
    get_retriever.cache_clear()
