"""Shared test configuration.

The environment is pinned to the offline backends (hashing embedder, in-memory
vector store, deterministic explainer) so the suite runs with no network access
and no model download. That is also how CI runs it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="demobank-tests-"))

os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["EMBEDDING_BACKEND"] = "hash"
os.environ["VECTOR_BACKEND"] = "memory"
os.environ["ENABLE_LLM"] = "false"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["CHROMA_DIR"] = str(_TMP / "chroma")
os.environ["CHROMA_COLLECTION"] = "test_policies"
os.environ["AUDIT_DB_URL"] = f"sqlite:///{(_TMP / 'audit.db').as_posix()}"
os.environ["DEFAULT_POLICY_VERSION"] = "2.0"
os.environ["RETRIEVAL_TOP_K"] = "5"

from app.audit.db import init_db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.rag.ingest import PolicyIngestor  # noqa: E402
from app.rag.retriever import PolicyRetriever, get_retriever  # noqa: E402
from app.rules.engine import RuleEngine  # noqa: E402
from app.rules.registry import PolicyRegistry, get_registry  # noqa: E402
from app.schemas import ApplicantProfile  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_environment() -> Iterator[None]:
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    yield


@pytest.fixture(scope="session")
def registry() -> PolicyRegistry:
    return get_registry()


@pytest.fixture(scope="session")
def engine(registry: PolicyRegistry) -> RuleEngine:
    return RuleEngine(registry)


@pytest.fixture(scope="session")
def indexed_retriever() -> PolicyRetriever:
    """The policy corpus, ingested once into the in-memory store."""
    retriever = get_retriever()
    PolicyIngestor(embedder=retriever.embedder, store=retriever.store).ensure_index()
    return retriever


@pytest.fixture(scope="session")
def golden_cases() -> List[Dict[str, Any]]:
    path = Path(__file__).parent / "golden" / "regression_cases.json"
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)["cases"]


@pytest.fixture
def clean_applicant() -> ApplicantProfile:
    """Passes every criterion in both policy versions (golden case A)."""
    return ApplicantProfile(
        applicant_reference="TEST-CLEAN",
        age=34,
        residency_status="CITIZEN",
        employment_status="FULL_TIME",
        employment_months=48,
        annual_income=90000,
        existing_monthly_debt=400,
        credit_score=720,
        credit_history_months=120,
        active_defaults=0,
        has_prior_bankruptcy=False,
        liquid_savings=15000,
        documents_verified=True,
        requested_amount=20000,
        loan_term_months=60,
    )


@pytest.fixture(scope="session")
def client(indexed_retriever: PolicyRetriever) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def parse_sse(body: str) -> List[Dict[str, Any]]:
    """Parse a buffered text/event-stream response into event dicts."""
    events: List[Dict[str, Any]] = []
    event_name = "message"
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            event_name = "message"
            continue
        if stripped.startswith("event:"):
            event_name = stripped[len("event:") :].strip()
        elif stripped.startswith("data:"):
            payload = stripped[len("data:") :].strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"raw": payload}
            events.append({"event": event_name, "data": data})
    return events
