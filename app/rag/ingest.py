"""Ingestion of the synthetic DemoBank policy corpus into the vector store.

Directory layout drives policy versioning::

    data/policies/v1.0/*.md   -> policy_version "1.0"
    data/policies/v2.0/*.md   -> policy_version "2.0"

Both versions live in the same collection and are separated at query time by a
metadata filter, so a version 1.0 enquiry can never be answered with a version
2.0 clause.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings
from app.rag.chunker import PolicyChunk, chunk_markdown, chunk_text
from app.rag.embeddings import Embedder, get_embedder
from app.rag.store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

MARKDOWN_SUFFIXES = {".md", ".markdown", ".txt"}
PDF_SUFFIXES = {".pdf"}


@dataclass
class IngestStats:
    documents: int = 0
    chunks: int = 0
    versions: Dict[str, int] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)
    embedder: str = ""
    backend: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "versions": self.versions,
            "skipped": self.skipped,
            "embedder": self.embedder,
            "vector_backend": self.backend,
        }


def _display_path(path: Path, root: Path) -> str:
    """Short, stable path shown in citations (e.g. ``policies/v2.0/01_...md``)."""
    try:
        return f"policies/{path.relative_to(root).as_posix()}"
    except ValueError:  # pragma: no cover - path outside the corpus root
        return str(path)


def _read_pdf(path: Path) -> List[tuple]:
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        logger.warning("PyMuPDF unavailable, skipping %s (%s)", path.name, exc)
        return []
    pages: List[tuple] = []
    with fitz.open(str(path)) as document:
        for index, page in enumerate(document, start=1):
            pages.append((index, page.get_text()))
    return pages


class PolicyIngestor:
    def __init__(
        self,
        embedder: Optional[Embedder] = None,
        store: Optional[VectorStore] = None,
    ) -> None:
        self.embedder = embedder or get_embedder()
        self.store = store or get_vector_store()

    # ------------------------------------------------------------------
    def build_chunks(self, policies_dir: Optional[Path] = None) -> List[PolicyChunk]:
        settings = get_settings()
        root = Path(policies_dir or settings.policies_dir)
        if not root.exists():
            raise FileNotFoundError(f"Policy corpus directory not found: {root}")

        chunks: List[PolicyChunk] = []
        for version_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            version = version_dir.name.lstrip("vV")
            for path in sorted(version_dir.iterdir()):
                if path.suffix.lower() in MARKDOWN_SUFFIXES:
                    text = path.read_text(encoding="utf-8")
                    chunks.extend(
                        chunk_markdown(
                            text,
                            source_path=_display_path(path, root),
                            policy_version=version,
                        )
                    )
                elif path.suffix.lower() in PDF_SUFFIXES:
                    pages = _read_pdf(path)
                    chunks.extend(
                        chunk_text(
                            pages,
                            source_path=str(path),
                            policy_version=version,
                            document_id=path.stem.upper(),
                            document_title=path.stem.replace("_", " ").title(),
                        )
                    )
        return chunks

    def ingest(
        self,
        policies_dir: Optional[Path] = None,
        rebuild: bool = False,
        batch_size: int = 64,
    ) -> IngestStats:
        if rebuild:
            self.store.reset()

        chunks = self.build_chunks(policies_dir)
        stats = IngestStats(embedder=self.embedder.name, backend=self.store.backend)
        documents = set()

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = self.embedder.embed_documents([chunk.text for chunk in batch])
            stats.chunks += self.store.upsert(batch, vectors)
            for chunk in batch:
                documents.add((chunk.policy_version, chunk.document_id))
                stats.versions[chunk.policy_version] = (
                    stats.versions.get(chunk.policy_version, 0) + 1
                )

        stats.documents = len(documents)
        logger.info(
            "Ingested %s chunks from %s documents using %s into %s",
            stats.chunks,
            stats.documents,
            self.embedder.name,
            self.store.backend,
        )
        return stats

    def ensure_index(self, policies_dir: Optional[Path] = None) -> IngestStats:
        """Ingest only when the collection is empty (safe to call at startup)."""
        if self.store.count() > 0:
            return IngestStats(
                documents=0,
                chunks=self.store.count(),
                embedder=self.embedder.name,
                backend=self.store.backend,
                skipped=["index already populated"],
            )
        return self.ingest(policies_dir)


def ingest_corpus(rebuild: bool = False) -> IngestStats:
    return PolicyIngestor().ingest(rebuild=rebuild)
