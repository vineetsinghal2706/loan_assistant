#!/usr/bin/env python3
"""Scaffolds the top-level README.md."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


README = r'''# Loan Eligibility Assistant

A streaming RAG chat application for personal-loan pre-qualification, built
around EXL's capstone brief: *"Pre-qualification conversations consume staff
time and produce inconsistent answers, because eligibility depends on rules
that sit in several documents and change on their own cycles."*

This repository builds the whole thing end to end:

- a **FastAPI** backend that streams token-by-token answers over SSE,
  grounded in policy documents via **hybrid (semantic + lexical) retrieval**,
  with a separate deterministic **rules engine** for the actual eligibility
  decision and full **audit logging** of every decision and the rule version
  behind it;
- a **Streamlit** chat UI with an applicant-profile form, live citations, and
  an audit-trail viewer;
- **Prometheus + Grafana** for latency/throughput/decision/error monitoring;
- a **Promptfoo** evaluation suite plus a numeric regression script wired
  into **GitHub Actions** so a regression in eligibility-answer accuracy
  blocks the merge;
- everything containerized with **Docker Compose**.

## Why this matters (mapping to the brief)

| Brief requirement | Where it lives |
|---|---|
| Streaming chat app over FastAPI | `backend/app/main.py` (`/v1/chat/stream`), `backend/app/streaming.py` |
| RAG over eligibility rules with grounded citations | `backend/app/rag/hybrid_retriever.py`, `backend/app/llm/prompts.py` |
| Stream tokens, handle mid-stream errors cleanly | `backend/app/streaming.py` (bounded queue + SSE `error` event) |
| Promptfoo gates in CI/CD blocking regressions | `promptfoo/promptfooconfig.yaml`, `.github/workflows/ci.yml` |
| Log every decision with the rule version, for audit | `backend/app/audit/logger.py`, `/v1/audit/recent` |
| Retrieve from a personal-loan document using both semantic and lexical search, respecting a policy *update* document | `backend/app/rag/hybrid_retriever.py` + `backend/data/policies/*` (see "Hybrid retrieval" below) |

## Architecture

```
                      +----------------------+
                      |   Streamlit frontend |
                      |  (chat + audit tab)  |
                      +----------+-----------+
                                 | SSE / HTTP
                                 v
+--------------------------------------------------------------+
|                        FastAPI backend                       |
|                                                                |
|  /v1/chat, /v1/chat/stream                                    |
|     |                                                          |
|     |--> HybridRetriever  --- semantic (sentence-transformers) |
|     |                     \__ lexical  (BM25)                  |
|     |                     --> Reciprocal Rank Fusion,           |
|     |                         biased to the latest policy       |
|     |                         version per section               |
|     |                                                          |
|     |--> RulesEngine  --- versioned YAML rule sets (v1, v2)     |
|     |                 --> deterministic outcome + rule ids      |
|     |                                                          |
|     |--> LLMClient  --- stub (offline, deterministic) or        |
|     |                   Anthropic (streamed, real)               |
|     |                   both grounded in retrieved excerpts +    |
|     |                   the computed decision                   |
|     |                                                          |
|     |--> AuditLogger --- JSONL, one record per request,         |
|     |                     includes rule_version + citations     |
|     |                                                          |
|     `--> /metrics  --- prometheus_client                       |
+---------------------------+------------------------------------+
                             |
                 +-----------+-----------+
                 |                       |
              Prometheus  <---------  Grafana
```

## Repository layout

```
backend/
  app/
    main.py              FastAPI app: chat, streaming, health, audit, metrics
    streaming.py         Bounded-queue SSE streaming with backpressure + error handling
    config.py            Settings (env vars / .env)
    models.py             Pydantic request/response/audit schemas
    metrics.py            Prometheus metric definitions
    rag/
      documents.py        Loads policy .md files, splits into versioned chunks
      hybrid_retriever.py Semantic + lexical (BM25) hybrid search, RRF, version bias
    eligibility/
      rules_engine.py     Loads versioned YAML rules, evaluates an applicant
    llm/
      client.py           Streaming LLM wrapper (stub | anthropic)
      prompts.py          Grounded system prompt + offline stub renderer
    audit/
      logger.py            Append-only JSONL audit log
  data/
    policies/              v1 policy + v2 policy-update documents
    eligibility_rules/      rules_v1.yaml, rules_v2.yaml
    eval/                   labelled_eligibility_set.jsonl (regression fixtures)
  scripts/
    build_index.py          (re)builds the hybrid retrieval index
    run_regression_eval.py  CI/CD accuracy + faithfulness gate
  tests/                    pytest unit + API tests
frontend/
  streamlit_app.py          Chat UI + audit trail tab
promptfoo/
  promptfooconfig.yaml       Promptfoo evaluation gate
monitoring/
  prometheus/, grafana/      Scrape config + provisioned dashboard
.github/workflows/ci.yml     Test -> build index -> promptfoo -> regression gate
docs/ci_evidence/            How a regression actually blocks a merge
docker-compose.yml, Makefile, .env.example
```

## Quickstart

```bash
cp .env.example .env
docker compose up --build
```

- Chat UI: http://localhost:8501
- API docs: http://localhost:8000/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin / admin, or anonymous viewer access)

The backend downloads the `all-MiniLM-L6-v2` sentence-transformer embedding
model on first run (needs outbound network access once; it is cached after
that). By default `LLM_PROVIDER=stub`, so the whole pipeline - retrieval,
rules, streaming, audit log, Grafana metrics - works with **no API key**.
Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...` in `.env` to get
real streamed answers from Claude instead of the deterministic stub text.

### Running without Docker

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example .env
python scripts/build_index.py
uvicorn app.main:app --reload

# in another terminal
cd frontend
pip install -r requirements.txt
BACKEND_URL=http://localhost:8000 streamlit run streamlit_app.py
```

## API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + current/available rule versions |
| GET | `/v1/policy/version` | Current rule version in force |
| POST | `/v1/chat` | Non-streaming answer (used by promptfoo/regression eval) |
| POST | `/v1/chat/stream` | SSE-streamed answer (`token`, `done`, `error` events) |
| GET | `/v1/audit/recent?n=20` | Recent audit records |
| GET | `/metrics` | Prometheus exposition |

Request body for `/v1/chat` and `/v1/chat/stream`:

```json
{
  "session_id": "abc123",
  "message": "Am I eligible for a personal loan given my profile?",
  "applicant": {
    "monthly_income": 40000,
    "monthly_debt": 5000,
    "credit_score": 720,
    "age": 30,
    "employment_type": "salaried",
    "requested_amount": 200000,
    "loan_tenure_months": 36
  }
}
```

`applicant` is optional - omit it (or send `null`) to ask a general policy
question without triggering a rules-engine decision.

## Hybrid retrieval over versioned policy documents

Your mentor's requirement was: when working from a real personal-loan
document, retrieve from it using **both semantic and lexical search**, and
respect a **policy update** document rather than just the original policy.
This is implemented, not simulated:

- `backend/data/policies/personal_loan_policy_v1.md` is the base policy.
- `backend/data/policies/personal_loan_policy_v2_update.md` is a policy
  *update* with the same section headings (income, DTI, credit score) but
  revised thresholds, tagged `version: v2`, `supersedes: v1` in its
  front-matter.
- `HybridRetriever` embeds every section with `sentence-transformers` for
  semantic similarity, indexes the same text with `rank_bm25` for lexical
  (keyword) search, and fuses both rankings with **Reciprocal Rank Fusion**.
  Each result is labelled `semantic`, `lexical`, or `hybrid` depending on
  which method(s) surfaced it, so citations show exactly how it was found.
- When a section exists in more than one version, older versions are
  down-weighted in the fused ranking, so a question like *"what is the
  minimum income requirement"* is answered from the v2 update, not the
  superseded v1 text - while v1 still exists in the index for audit/history.

To point this at your own real policy PDF/DOCX instead of the synthetic
markdown here: convert it to markdown with the same
`---\ndoc_id / version / effective_date / supersedes\n---` front-matter
convention, drop it in `backend/data/policies/`, and run
`python backend/scripts/build_index.py`.

## Eligibility rules and audit logging

`backend/app/eligibility/rules_engine.py` evaluates a small, versioned set of
deterministic rules (income floor, debt-to-income cap, credit score,
age, employment type) from `backend/data/eligibility_rules/rules_v{1,2}.yaml`.
This is deliberately separate from the LLM: the *decision* is always
deterministic and reproducible, and the LLM's job is only to explain it in
natural language, grounded in the retrieved policy excerpts. Every request
to `/v1/chat` or `/v1/chat/stream` appends one JSON record to
`backend/data/audit/audit_log.jsonl` containing the question, the rule
version applied, the full decision (outcome + reasons + rule ids), the
citations used, and the latency - viewable live in the Streamlit "Audit
trail" tab or via `GET /v1/audit/recent`.

## Streaming and backpressure

`/v1/chat/stream` returns Server-Sent Events. Internally
(`backend/app/streaming.py`), the LLM token producer and the HTTP consumer
are decoupled by a bounded `asyncio.Queue(maxsize=32)`: if the client reads
slowly, `queue.put()` blocks, which propagates backpressure all the way back
to token generation instead of buffering an unbounded amount of text in
memory. If the LLM provider fails mid-stream (timeout, API error), the
producer catches it, increments `loan_assistant_stream_errors_total`, and
emits a single SSE `error` event so the client can show a clean "response
was interrupted" message instead of a hung or broken connection.

## CI/CD evaluation gate (Promptfoo + regression script)

`.github/workflows/ci.yml` runs, in order: pytest unit tests, index build,
API startup, then two independent gates:

1. **`promptfoo eval`** against `promptfoo/promptfooconfig.yaml` - assertion
   based (does the answer contain the right outcome and rule id/citation).
2. **`backend/scripts/run_regression_eval.py`** - replays
   `backend/data/eval/labelled_eligibility_set.jsonl` and computes
   eligibility-answer accuracy and citation faithfulness, failing (non-zero
   exit) if either drops below a configurable threshold.

Either failing fails the GitHub Actions check, which blocks the merge if
branch protection requires it. `docs/ci_evidence/blocked_merge_example.md`
walks through a concrete example (a rule-file regression) and exactly what
each gate reports when it catches it, plus how to reproduce it locally.

## Monitoring

`/metrics` exposes Prometheus counters/histograms for request latency (by
endpoint), retrieval latency, eligibility decisions (by outcome and rule
version), tokens streamed, and mid-stream errors. `monitoring/prometheus/`
scrapes the backend every 5s; `monitoring/grafana/` auto-provisions a
"Loan Eligibility Assistant" dashboard with panels for p95 latency,
throughput, decisions by outcome, errors, and retrieval latency.

## Success metrics (from the brief)

| Metric | How it is measured |
|---|---|
| Eligibility-answer accuracy | `run_regression_eval.py` vs. the labelled set |
| Faithfulness | Same script: checks the answer actually cites the expected rule ids / policy keywords |
| Blocked-merge rate on regressions | CI job exit code from promptfoo + the regression script |
| Latency | `loan_assistant_request_latency_seconds` (Prometheus/Grafana), and p95 printed by the regression script |

## Testing

```bash
cd backend
pytest tests -v
```

`test_rules_engine.py` is pure unit testing (no network). `test_api.py` and
`test_hybrid_retriever.py` build the retrieval index in-process, which
downloads the embedding model on first run.

## Known limitations / notes for production hardening

- Rule conditions in the YAML rule files are evaluated with Python `eval`
  against a restricted namespace (no builtins). This is safe because the
  conditions are developer-authored config, not user input - do not extend
  this pattern to evaluate untrusted input without a proper expression
  sandbox.
- The `stub` LLM provider produces templated, deterministic text so the
  whole pipeline is testable offline. Switch to `LLM_PROVIDER=anthropic` for
  real generated answers; you may want to add a second real-LLM-based
  faithfulness check (e.g. an LLM-graded rubric in promptfoo) once you have
  an API key wired into CI as a secret.
- The embedding index is brute-force cosine similarity over NumPy arrays
  rather than an ANN index (FAISS/etc.) - simple and fast enough for a
  policy corpus of this size, but worth revisiting for a much larger
  document set.
- `backend/data/index/` and `backend/data/audit/` are gitignored generated
  artifacts; the index is rebuilt on first startup (or via
  `python scripts/build_index.py` / the Docker build step) and the audit log
  starts empty.
'''

if __name__ == "__main__":
    write_file("README.md", README)
    print("\n1 file written.")
