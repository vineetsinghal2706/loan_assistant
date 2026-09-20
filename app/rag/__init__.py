"""Retrieval-augmented generation over the synthetic DemoBank policy corpus.

The RAG layer answers exactly one question: *what does the policy say?* It
never decides eligibility.
"""

from app.rag.chunker import PolicyChunk, chunk_markdown, chunk_text, parse_front_matter
from app.rag.embeddings import Embedder, get_embedder
from app.rag.ingest import PolicyIngestor, IngestStats
from app.rag.retriever import PolicyRetriever, get_retriever
from app.rag.store import VectorStore, get_vector_store

__all__ = [
    "PolicyChunk",
    "chunk_markdown",
    "chunk_text",
    "parse_front_matter",
    "Embedder",
    "get_embedder",
    "PolicyIngestor",
    "IngestStats",
    "PolicyRetriever",
    "get_retriever",
    "VectorStore",
    "get_vector_store",
]
