#!/usr/bin/env python3
"""Scaffolds backend/app/{rag,eligibility,llm,audit} subpackages."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

# ---------------------------------------------------------------- rag -----
FILES["backend/app/rag/__init__.py"] = r'''
'''

FILES["backend/app/rag/documents.py"] = r'''import re
from dataclasses import dataclass
from pathlib import Path

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
SECTION_SPLIT_RE = re.compile(r"(?m)^## ")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    version: str
    effective_date: str
    section: str
    text: str


def _parse_front_matter(raw: str):
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta_block, body = match.group(1), match.group(2)
    meta = {}
    for line in meta_block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            meta[key.strip()] = value if value and value.lower() != "null" else None
    return meta, body


def load_policy_chunks(policy_dir: Path) -> list[Chunk]:
    """Loads every *.md file in policy_dir, splits it on "## " section
    headers, and tags each resulting chunk with the doc_id/version/
    effective_date declared in its YAML front-matter. Documents that update
    an earlier policy (e.g. a "v2" file that supersedes "v1") simply reuse
    the same section headings, which lets the retriever later prefer the
    newer version for any section that exists in both.
    """
    chunks: list[Chunk] = []
    for path in sorted(policy_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(raw)
        doc_id = meta.get("doc_id") or path.stem
        version = meta.get("version") or "v1"
        effective_date = meta.get("effective_date") or "unknown"

        parts = SECTION_SPLIT_RE.split(body)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.splitlines()
            title = lines[0].strip()
            text = "\n".join(lines[1:]).strip()
            if not text:
                continue
            chunk_id = f"{doc_id}:{version}:{title}"
            chunks.append(Chunk(chunk_id, doc_id, version, effective_date, title, text))
    return chunks
'''

FILES["backend/app/rag/hybrid_retriever.py"] = r'''import pickle
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from .documents import Chunk, load_policy_chunks


def _tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in text.split() if tok.strip()]


class HybridRetriever:
    """Combines dense/semantic search (sentence-transformer embeddings +
    cosine similarity) with sparse/lexical search (BM25) via Reciprocal Rank
    Fusion, and biases the fused ranking towards the most recently effective
    policy version for any section that appears in more than one version.
    This is what lets the assistant answer from an updated policy document
    rather than a superseded one, while still citing both semantic and
    lexical matches.
    """

    def __init__(self, embedding_model_name: str, index_dir: Path):
        self.index_dir = index_dir
        self.model = SentenceTransformer(embedding_model_name)
        self.chunks: list[Chunk] = []
        self.embeddings: "np.ndarray | None" = None
        self.bm25: "BM25Okapi | None" = None

    def build(self, policy_dir: Path) -> None:
        self.chunks = load_policy_chunks(policy_dir)
        if not self.chunks:
            raise ValueError(f"No policy chunks found in {policy_dir}")
        texts = [c.text for c in self.chunks]
        self.embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        self.bm25 = BM25Okapi([_tokenize(t) for t in texts])
        self._persist()

    def _persist(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        with open(self.index_dir / "chunks.pkl", "wb") as f:
            pickle.dump(self.chunks, f)
        np.save(self.index_dir / "embeddings.npy", self.embeddings)

    def load(self) -> bool:
        chunks_path = self.index_dir / "chunks.pkl"
        emb_path = self.index_dir / "embeddings.npy"
        if not (chunks_path.exists() and emb_path.exists()):
            return False
        with open(chunks_path, "rb") as f:
            self.chunks = pickle.load(f)
        self.embeddings = np.load(emb_path)
        self.bm25 = BM25Okapi([_tokenize(c.text) for c in self.chunks])
        return True

    def _semantic_ranked(self, query: str) -> list[int]:
        q_emb = self.model.encode([query], normalize_embeddings=True)[0]
        scores = self.embeddings @ q_emb
        return list(np.argsort(-scores))

    def _lexical_ranked(self, query: str) -> list[int]:
        scores = self.bm25.get_scores(_tokenize(query))
        return list(np.argsort(-np.array(scores)))

    def search(
        self,
        query: str,
        top_k: int = 5,
        prefer_latest_version: bool = True,
        rrf_k: int = 60,
    ) -> list[dict]:
        sem_rank = self._semantic_ranked(query)
        lex_rank = self._lexical_ranked(query)

        rrf_scores: dict[int, float] = {}
        for rank, idx in enumerate(sem_rank):
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)
        for rank, idx in enumerate(lex_rank):
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)

        if prefer_latest_version:
            latest_version_for_section: dict[str, str] = {}
            for chunk in self.chunks:
                key = f"{chunk.doc_id}:{chunk.section}"
                current = latest_version_for_section.get(key)
                if current is None or chunk.version > current:
                    latest_version_for_section[key] = chunk.version
            for idx, chunk in enumerate(self.chunks):
                key = f"{chunk.doc_id}:{chunk.section}"
                if chunk.version != latest_version_for_section[key]:
                    rrf_scores[idx] = rrf_scores.get(idx, 0.0) * 0.4

        ranked_idx = sorted(rrf_scores.keys(), key=lambda i: -rrf_scores[i])[:top_k]

        sem_set = set(sem_rank[:top_k])
        lex_set = set(lex_rank[:top_k])
        results = []
        for idx in ranked_idx:
            chunk = self.chunks[idx]
            if idx in sem_set and idx in lex_set:
                source_type = "hybrid"
            elif idx in sem_set:
                source_type = "semantic"
            else:
                source_type = "lexical"
            results.append(
                {
                    "doc_id": chunk.doc_id,
                    "version": chunk.version,
                    "section": chunk.section,
                    "excerpt": chunk.text[:400],
                    "source_type": source_type,
                    "score": float(rrf_scores[idx]),
                }
            )
        return results
'''

# --------------------------------------------------------- eligibility ----
FILES["backend/app/eligibility/__init__.py"] = r'''
'''

FILES["backend/app/eligibility/rules_engine.py"] = r'''from pathlib import Path

import yaml

from ..models import ApplicantProfile, EligibilityDecision

# Rule conditions are short boolean expressions authored by developers in the
# YAML rule files under backend/data/eligibility_rules/ (not user input).
# They are evaluated with no builtins available, against only the applicant
# fields, which is adequate isolation for this trusted, developer-authored
# configuration. Do not extend this to evaluate untrusted input.
_EVAL_GLOBALS = {"__builtins__": {}}

# Below this credit score, an R-CS-01 failure is treated as an automatic
# decline. Between this value and the rule's own threshold, it is routed to
# manual review ("needs_review") instead of an automatic decline.
_SOFT_REVIEW_CREDIT_FLOOR = 630


class RulesEngine:
    def __init__(self, rules_dir: Path):
        self.rules_dir = rules_dir
        self.rule_sets: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.rules_dir.glob("rules_*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.rule_sets[data["version"]] = data

    def available_versions(self) -> list[str]:
        return sorted(self.rule_sets.keys())

    def get_ruleset(self, version: str) -> dict:
        if version not in self.rule_sets:
            raise KeyError(f"Unknown rule version '{version}'. Available: {self.available_versions()}")
        return self.rule_sets[version]

    def latest_version(self) -> str:
        return sorted(self.rule_sets, key=lambda v: self.rule_sets[v]["effective_date"])[-1]

    def evaluate(self, applicant: ApplicantProfile, version: str) -> EligibilityDecision:
        ruleset = self.get_ruleset(version)
        context = applicant.model_dump()

        failed: list[dict] = []
        soft_failed: list[dict] = []
        rule_ids: list[str] = []

        for rule in ruleset["rules"]:
            rule_ids.append(rule["id"])
            try:
                passed = eval(rule["condition"], _EVAL_GLOBALS, context)  # noqa: S307
            except Exception as exc:  # pragma: no cover - defensive only
                passed = False
                rule = {**rule, "description": f"{rule['description']} (evaluation error: {exc})"}

            if passed:
                continue

            if rule["id"] == "R-CS-01" and context["credit_score"] >= _SOFT_REVIEW_CREDIT_FLOOR:
                soft_failed.append(rule)
            else:
                failed.append(rule)

        if not failed and not soft_failed:
            outcome = "eligible"
        elif not failed and soft_failed:
            outcome = "needs_review"
        else:
            outcome = "not_eligible"

        if outcome == "eligible":
            reasons = [f"All rules satisfied under policy {version} ({ruleset['effective_date']})."]
        else:
            reasons = [f"{r['id']}: {r['description']} ({r['section']})" for r in failed + soft_failed]

        return EligibilityDecision(
            outcome=outcome,
            reasons=reasons,
            rule_ids=rule_ids,
            rule_version=version,
        )
'''

# ----------------------------------------------------------------- llm ----
FILES["backend/app/llm/__init__.py"] = r'''
'''

FILES["backend/app/llm/prompts.py"] = r'''from typing import Optional

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
'''

FILES["backend/app/llm/client.py"] = r'''import asyncio
from typing import AsyncGenerator, Optional

from .prompts import build_system_prompt, render_stub_answer


class LLMClient:
    """Thin streaming wrapper around either the real Anthropic API or a fully
    offline, deterministic stub. The stub exists so the whole pipeline (RAG +
    rules + streaming + audit logging + CI regression gate) can be exercised
    without any API key - flip LLM_PROVIDER=anthropic once you have one.
    """

    def __init__(self, provider: str, model: str, api_key: Optional[str]):
        self.provider = provider
        self.model = model
        self.api_key = api_key

    async def stream_answer(
        self,
        question: str,
        rule_version: str,
        decision: Optional[object],
        chunks: list[dict],
    ) -> AsyncGenerator[str, None]:
        if self.provider == "anthropic":
            async for token in self._stream_anthropic(question, rule_version, decision, chunks):
                yield token
        else:
            async for token in self._stream_stub(question, rule_version, decision, chunks):
                yield token

    async def _stream_anthropic(self, question, rule_version, decision, chunks):
        import anthropic  # imported lazily so the stub path has no hard dependency

        system_prompt = build_system_prompt(rule_version, decision, chunks)
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        async with client.messages.stream(
            model=self.model,
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _stream_stub(self, question, rule_version, decision, chunks):
        answer = render_stub_answer(question, rule_version, decision, chunks)
        for word in answer.split(" "):
            await asyncio.sleep(0)  # yield control, simulating token-by-token streaming
            yield word + " "
'''

# --------------------------------------------------------------- audit ----
FILES["backend/app/audit/__init__.py"] = r'''
'''

FILES["backend/app/audit/logger.py"] = r'''import json
import threading
from pathlib import Path

from ..models import AuditRecord

_lock = threading.Lock()


class AuditLogger:
    """Append-only JSON-Lines audit log. Every chat decision is logged with
    the rule version applied, the citations used to ground the answer, and
    the latency, so any eligibility decision can be reconstructed and
    reviewed later.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: AuditRecord) -> None:
        line = record.model_dump_json()
        with _lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(line) for line in lines]
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
