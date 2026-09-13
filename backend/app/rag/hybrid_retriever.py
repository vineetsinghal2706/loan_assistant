import pickle
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
