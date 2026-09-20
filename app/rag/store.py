"""Vector store abstraction.

ChromaDB is the primary backend. A small JSON-backed in-memory store is used
when ChromaDB is not installed or cannot open its persistent directory, so the
pipeline degrades instead of failing - important for CI and for classroom
machines.
"""

from __future__ import annotations

import json
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.config import Settings, get_settings
from app.rag.chunker import PolicyChunk

logger = logging.getLogger(__name__)


@dataclass
class StoredMatch:
    chunk_id: str
    text: str
    metadata: Dict[str, Any]
    score: float


def _chroma_where(where: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not where:
        return None
    clauses = [{key: {"$eq": value}} for key, value in where.items()]
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _matches_filter(metadata: Mapping[str, Any], where: Optional[Mapping[str, Any]]) -> bool:
    if not where:
        return True
    return all(metadata.get(key) == value for key, value in where.items())


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorStore(ABC):
    backend: str = "abstract"

    @abstractmethod
    def upsert(self, chunks: Sequence[PolicyChunk], vectors: Sequence[Sequence[float]]) -> int:
        ...

    @abstractmethod
    def query(
        self,
        vector: Sequence[float],
        top_k: int = 5,
        where: Optional[Mapping[str, Any]] = None,
    ) -> List[StoredMatch]:
        ...

    @abstractmethod
    def count(self, where: Optional[Mapping[str, Any]] = None) -> int:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...


class ChromaVectorStore(VectorStore):
    backend = "chromadb"

    def __init__(self, persist_dir: Path, collection_name: str) -> None:
        import chromadb  # noqa: PLC0415

        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection_name = collection_name
        self._collection = self._client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    def upsert(self, chunks: Sequence[PolicyChunk], vectors: Sequence[Sequence[float]]) -> int:
        if not chunks:
            return 0
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            embeddings=[list(map(float, vector)) for vector in vectors],
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.to_metadata() for chunk in chunks],
        )
        return len(chunks)

    def query(
        self,
        vector: Sequence[float],
        top_k: int = 5,
        where: Optional[Mapping[str, Any]] = None,
    ) -> List[StoredMatch]:
        result = self._collection.query(
            query_embeddings=[list(map(float, vector))],
            n_results=max(1, top_k),
            where=_chroma_where(where),
            include=["documents", "metadatas", "distances"],
        )
        matches: List[StoredMatch] = []
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for index, chunk_id in enumerate(ids):
            distance = float(distances[index]) if index < len(distances) else 1.0
            matches.append(
                StoredMatch(
                    chunk_id=str(chunk_id),
                    text=str(documents[index]) if index < len(documents) else "",
                    metadata=dict(metadatas[index]) if index < len(metadatas) else {},
                    score=round(1.0 / (1.0 + max(distance, 0.0)), 6),
                )
            )
        return matches

    def count(self, where: Optional[Mapping[str, Any]] = None) -> int:
        if not where:
            return int(self._collection.count())
        found = self._collection.get(where=_chroma_where(where), include=[])
        return len(found.get("ids") or [])

    def reset(self) -> None:
        try:
            self._client.delete_collection(self._collection_name)
        except Exception:  # pragma: no cover - collection may not exist
            pass
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name, metadata={"hnsw:space": "cosine"}
        )


class InMemoryVectorStore(VectorStore):
    """Deterministic fallback store with optional JSON persistence."""

    backend = "memory"

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._records: Dict[str, Dict[str, Any]] = {}
        self._persist_path = persist_path
        self._load()

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with self._persist_path.open("r", encoding="utf-8") as handle:
                self._records = json.load(handle)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not load in-memory index from disk: %s", exc)
            self._records = {}

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._persist_path.open("w", encoding="utf-8") as handle:
                json.dump(self._records, handle)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not persist in-memory index: %s", exc)

    def upsert(self, chunks: Sequence[PolicyChunk], vectors: Sequence[Sequence[float]]) -> int:
        for chunk, vector in zip(chunks, vectors):
            self._records[chunk.chunk_id] = {
                "text": chunk.text,
                "metadata": chunk.to_metadata(),
                "vector": [float(value) for value in vector],
            }
        self._save()
        return len(chunks)

    def query(
        self,
        vector: Sequence[float],
        top_k: int = 5,
        where: Optional[Mapping[str, Any]] = None,
    ) -> List[StoredMatch]:
        scored: List[StoredMatch] = []
        for chunk_id, record in self._records.items():
            if not _matches_filter(record["metadata"], where):
                continue
            scored.append(
                StoredMatch(
                    chunk_id=chunk_id,
                    text=record["text"],
                    metadata=dict(record["metadata"]),
                    score=round(_cosine(vector, record["vector"]), 6),
                )
            )
        scored.sort(key=lambda match: (-match.score, match.chunk_id))
        return scored[: max(1, top_k)]

    def count(self, where: Optional[Mapping[str, Any]] = None) -> int:
        if not where:
            return len(self._records)
        return sum(
            1 for record in self._records.values() if _matches_filter(record["metadata"], where)
        )

    def reset(self) -> None:
        self._records = {}
        self._save()


def build_vector_store(settings: Optional[Settings] = None) -> VectorStore:
    settings = settings or get_settings()
    backend = (settings.vector_backend or "auto").strip().lower()
    memory_path = settings.chroma_dir / f"{settings.chroma_collection}_memory.json"

    if backend == "memory":
        return InMemoryVectorStore(memory_path)

    if backend in {"auto", "chroma", "chromadb"}:
        try:
            return ChromaVectorStore(settings.chroma_dir, settings.chroma_collection)
        except Exception as exc:  # pragma: no cover - depends on environment
            if backend != "auto":
                raise
            logger.warning(
                "ChromaDB unavailable (%s); falling back to the in-memory vector store.",
                exc,
            )
            return InMemoryVectorStore(memory_path)

    raise ValueError(f"Unknown VECTOR_BACKEND '{settings.vector_backend}'")


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    return build_vector_store()


def reset_store_cache() -> None:
    get_vector_store.cache_clear()
