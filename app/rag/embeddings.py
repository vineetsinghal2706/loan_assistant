"""Embedding backends.

Two implementations:

* :class:`SentenceTransformerEmbedder` - ``all-MiniLM-L6-v2`` by default, used
  whenever the model is importable and loadable.
* :class:`HashingEmbedder` - a deterministic, dependency-free feature-hashing
  embedder used as a fallback so tests, CI and air-gapped demos never need to
  download a model. Retrieval quality is lower but behaviour is identical and
  fully reproducible.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import List, Optional, Sequence

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
DEFAULT_DIMENSION = 384


class Embedder(ABC):
    """Common interface for embedding backends."""

    name: str = "abstract"
    dimension: int = DEFAULT_DIMENSION

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        ...

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


def _l2_normalise(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


class HashingEmbedder(Embedder):
    """Deterministic feature-hashing embedder (offline fallback)."""

    name = "hashing-embedder-v1"

    def __init__(self, dimension: int = DEFAULT_DIMENSION) -> None:
        self.dimension = dimension

    @staticmethod
    def _tokens(text: str) -> List[str]:
        lowered = text.lower()
        words = TOKEN_RE.findall(lowered)
        tokens: List[str] = list(words)
        # word bigrams keep a little phrase structure ("credit score")
        tokens.extend(f"{a}_{b}" for a, b in zip(words, words[1:]))
        return tokens

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimension

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            tokens = self._tokens(text or "")
            for token in tokens:
                index = self._bucket(token)
                # sign hashing reduces collision bias
                sign = 1.0 if self._bucket(token + "#sign") % 2 == 0 else -1.0
                vector[index] += sign
            # sublinear scaling, then normalise
            vector = [math.copysign(math.log1p(abs(v)), v) for v in vector]
            vectors.append(_l2_normalise(vector))
        return vectors


class SentenceTransformerEmbedder(Embedder):
    """sentence-transformers backend."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers/{model_name.split('/')[-1]}"
        self.dimension = int(self._model.get_sentence_embedding_dimension())

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [[float(value) for value in vector] for vector in vectors]


def build_embedder(settings: Optional[Settings] = None) -> Embedder:
    settings = settings or get_settings()
    backend = (settings.embedding_backend or "auto").strip().lower()

    if backend == "hash":
        return HashingEmbedder()

    if backend in {"auto", "sentence-transformers", "sentence_transformers", "st"}:
        try:
            return SentenceTransformerEmbedder(settings.embedding_model)
        except Exception as exc:  # pragma: no cover - depends on environment
            if backend != "auto":
                raise
            logger.warning(
                "sentence-transformers unavailable (%s); falling back to the "
                "deterministic hashing embedder.",
                exc,
            )
            return HashingEmbedder()

    raise ValueError(f"Unknown EMBEDDING_BACKEND '{settings.embedding_backend}'")


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return build_embedder()


def reset_embedder_cache() -> None:
    get_embedder.cache_clear()
