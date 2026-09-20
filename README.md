# DemoBank Loan Eligibility Assistant

> **DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY**
>
> DemoBank is a fictional institution. Every policy document, threshold and
> applicant in this repository was invented for an AI Services Capstone Project
> in Banking. There is **no real customer data**, **no confidential bank
> document** and **no proprietary eligibility rule** anywhere in it. Nothing
> here is a credit decision, and nothing here is financial advice.

A streaming conversational loan **pre-qualification** assistant that answers
"am I eligible, and why?" with a decision that a bank could actually defend:
deterministic, versioned, cited and auditable.

---

## 1. The business problem

Pre-qualification conversations consume staff time and produce inconsistent
answers, because eligibility depends on rules spread across several policy
documents, and those rules change over time. An applicant who enquired in March
2025 and again in September 2026 can legitimately get two different answers -
and the bank must be able to explain why.

Handing this to an LLM by asking *"is this customer eligible?"* fails three
tests at once: the answer is not reproducible, it cannot be traced to a clause,
and it silently applies whichever version of the policy happened to be in the
context window.

## 2. Core design principle

Three responsibilities, three components, and one of them is not the LLM.

| Question | Component | Guarantee |
| --- | --- | --- |
| *What does the policy say?* | RAG over the versioned policy corpus | Retrieval is filtered to the policy version being applied. |
| *Does this applicant satisfy those rules?* | Deterministic rule engine | Same input + same version => same outcome and same `decision_hash`. |
| *How should the result be explained?* | Claude | Receives the final outcome and the retrieved clauses; may not change either. |

**Claude never determines eligibility.** The rule engine is the source of truth.
The API streams the decision *before* the first generated token, the explanation
is checked against the decision after generation, and any contradiction is
replaced by a deterministic summary, counted in Prometheus and recorded in the
audit log.

## 3. Pipeline

```
Applicant data (form) ─┐
                       ├─> Document extraction (provenance per field)
Applicant documents ───┘            │
                                    v
                        Deterministic rule engine  <── versioned rule packs (YAML)
                                    │                    v1.0 / v2.0
                                    │ outcome is final here
                                    v
                        RAG retrieval, scoped to the policy version applied
                                    │            (ChromaDB + sentence-transformers)
                                    v
                        Claude explanation (streaming, citations required)
                                    │
                                    v
              Guardrails ─> SQLite audit log ─> /metrics ─> Prometheus ─> Grafana
```

Server-sent events arrive in this order, which is itself part of the design:

```
trace -> policy -> decision -> citations -> token* -> guardrail -> audit -> done
```

## 4. Architecture

```
                         ┌──────────────────────┐
                         │     Streamlit UI     │  :8501
                         └──────────┬───────────┘
                                    │  SSE / JSON
                                    v
                         ┌──────────────────────┐
                         │       FastAPI        │  :8000
                         │   streaming API      │
                         └──────────┬───────────┘
                 ┌──────────────────┼──────────────────┐
                 v                  v                  v
          ┌────────────┐    ┌──────────────┐   ┌──────────────┐
          │    RAG     │    │ Rule engine  │   │  Claude LLM  │
          │ retriever  │    │deterministic │   │ explanation  │
          └─────┬──────┘    └──────┬───────┘   └──────────────┘
                v                  v
          ┌────────────┐     ┌──────────────┐
          │  ChromaDB  │     │  applicant   │
          │ vector DB  │     │  facts       │
          └─────┬──────┘     └──────────────┘
                v
       ┌────────────────────┐
       │ synthetic policies │
       │  v1.0 and v2.0     │
       └────────────────────┘

FastAPI ──> /metrics ──> Prometheus ──> Grafana        (:9090, :3000)
FastAPI ──> SQLite audit database
GitHub  ──> GitHub Actions: ruff, policy integrity, pytest, RAG tests,
            regression suite, promptfoo, Docker build + smoke test
```

## 5. Quick start

### Option A - Docker Compose (everything, including dashboards)

```bash
cp .env.example .env          # optional: add ANTHROPIC_API_KEY
docker compose up --build
```

| Service | URL |
| --- | --- |
| API docs | http://localhost:8000/docs |
| Streamlit UI | http://localhost:8501 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / demobank) |

### Option B - local Python

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env

python scripts/validate_policies.py     # rule packs vs policy corpus
python scripts/ingest_policies.py       # build the vector index
uvicorn app.main:app --reload           # API  on :8000
streamlit run ui/streamlit_app.py       # UI   on :8501
```

No API key, no network? The assistant degrades deliberately rather than
failing:

| Missing | Fallback | Effect |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | deterministic template explainer | wording is fixed rather than fluent; decision identical |
| `sentence-transformers` model | deterministic hashing embedder | lower retrieval quality; version filtering unaffected |
| `chromadb` | JSON-backed in-memory vector store | same API, no persistence beyond the file |

Force the offline path with `EMBEDDING_BACKEND=hash VECTOR_BACKEND=memory ENABLE_LLM=false`.
That is exactly how CI runs.

### See the whole pipeline in one command

```bash
make demo          # or: python scripts/run_demo.py --compare
```

It extracts each synthetic applicant document, prints every criterion with its
status and clause, shows the citations, streams the explanation and reports the
audit id - then re-runs the applicant against both policy versions.

## 6. API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health`, `/health/ready` | status, backends in use, indexed clause count |
| GET | `/policies/versions` | every registered version with its change log |
| GET | `/policies/versions/{v}/rules` | the machine-readable criteria for a version |
| GET | `/policies/search?q=&policy_version=` | RAG search, scoped to one version |
| POST | `/policies/reindex` | re-ingest the corpus |
| POST | `/documents/extract` | upload an applicant pack, get fields + provenance |
| POST | `/documents/samples/{name}/extract` | extract a bundled synthetic pack |
| POST | `/eligibility/evaluate` | **deterministic decision + citations, no LLM** |
| POST | `/eligibility/explain` | decision plus a complete, guardrail-checked explanation |
| POST | `/eligibility/stream` | SSE: decision first, then streamed explanation |
| POST | `/eligibility/compare-versions` | one applicant, every policy version, with a diff |
| POST | `/chat/stream`, `/chat` | conversational form of the same pipeline |
| GET | `/audit/decisions`, `/audit/decisions/{id}`, `/audit/summary` | audit log |
| GET | `/metrics` | Prometheus exposition |

Example - the same applicant, two policy versions:

```bash
APPLICANT='{"age":41,"residency_status":"PERMANENT_RESIDENT","employment_status":"FULL_TIME",
"employment_months":36,"annual_income":72000,"existing_monthly_debt":350,"credit_score":645,
"credit_history_months":90,"active_defaults":0,"has_prior_bankruptcy":false,
"liquid_savings":12000,"documents_verified":true,"requested_amount":18000,"loan_term_months":60}'

curl -s -X POST localhost:8000/eligibility/evaluate -H 'content-type: application/json' \
  -d "{\"applicant\":$APPLICANT,\"policy_version\":\"1.0\"}" | jq .decision.outcome
# "ELIGIBLE"          - v1.0 minimum score is 640

curl -s -X POST localhost:8000/eligibility/evaluate -H 'content-type: application/json' \
  -d "{\"applicant\":$APPLICANT,\"policy_version\":\"2.0\"}" | jq .decision.outcome
# "NOT_ELIGIBLE"      - v2.0 minimum score is 660 (CR-2.1)
```

Streaming:

```bash
curl -N -X POST localhost:8000/chat/stream -H 'content-type: application/json' \
  -d "{\"applicant\":$APPLICANT,\"question\":\"Why has the answer changed?\"}"
```

## 7. Policy versioning

Two complete synthetic policy corpora live side by side, each with its own
machine-readable rule pack:

| | v1.0 | v2.0 |
| --- | --- | --- |
| In force | 2023-01-01 to 2025-06-30 | 2025-07-01 onwards |
| Personal loan minimum score (CR-2.1) | 640 | **660** |
| Personal loan maximum DTI (CR-3.1) | 45% | **40%** |
| Stressed affordability (CR-3.3) | - | **+2.00 pp, capped at 50%** |
| Minimum employment tenure (PL-3.2) | 6 months | **12 months** |
| Personal loan income multiple (PL-4.2) | 5.0x | **4.5x** |
| Savings buffer (PL-5.2) | - | **3x monthly obligations (supporting)** |
| Mortgage maximum LTV (MG-5.1) | 90% | **85%**, or 90% for a first-time buyer scoring 720+ (MG-5.4) |
| Bankruptcy discharge window (CR-4.1) | 5 years | **4 years** |

Version resolution, in priority order: an explicit `policy_version`, then
`as_of_date` (the version in force on the enquiry date), then the configured
default. The version applied is returned in every response, embedded in every
citation, and stored on every audit row. See
[docs/POLICY_VERSIONING.md](docs/POLICY_VERSIONING.md).

## 8. Four outcomes, and why the fourth matters

| Outcome | Meaning |
| --- | --- |
| `ELIGIBLE` | every mandatory and supporting criterion satisfied |
| `ELIGIBLE_WITH_CONDITIONS` | mandatory criteria satisfied; a supporting criterion needs verification or officer review |
| `NOT_ELIGIBLE` | at least one mandatory criterion failed |
| `INSUFFICIENT_INFORMATION` | a mandatory criterion could not be evaluated |

A missing credit score is not a decline. The condition language is tri-state
(`true` / `false` / `unknown`), so absent data produces
`INSUFFICIENT_INFORMATION` plus a list of exactly what is needed - and a
guardrail forbids the explanation from wording it as a rejection.

## 9. Testing and evaluation

```bash
make test          # whole suite, offline backends
make regression    # golden decisions + policy versioning only
make eval          # promptfoo explanation faithfulness
make validate      # rule packs vs the written policy corpus
```

| Suite | What it protects |
| --- | --- |
| `tests/test_facts.py` | amortisation, DTI, LTV, and None-safety |
| `tests/test_predicates.py` | tri-state condition logic |
| `tests/test_rule_engine.py` | outcome mapping, capacity, decision-hash stability |
| `tests/test_policy_versioning.py` | version resolution, effective dates, explained diffs |
| `tests/test_rag.py` | clause chunking, deterministic embeddings, **version isolation** |
| `tests/test_extraction.py` | field accuracy, provenance, honest missing-field reporting |
| `tests/test_llm_guardrails.py` | contradiction, commitment language, invented citations |
| `tests/test_api.py` | endpoints and the SSE event ordering contract |
| `tests/test_regression_golden.py` | 11 hand-authored cases x 2 versions |
| `promptfoo/` | explanation faithfulness, including jailbreak attempts |

The regression suite is the interesting one: a change to any threshold in a rule
pack will move a golden outcome, so a policy change cannot be made silently.
See [docs/EVALUATION.md](docs/EVALUATION.md).

## 10. Monitoring

`/metrics` exposes decisions by outcome/product/version, per-criterion
evaluations and failures, rule-engine duration, retrieval latency and empty
retrievals, citation counts, explanation latency and token usage by mode,
LLM fallbacks, **guardrail violations**, extraction hit/miss per field, audit
writes and write errors, plus HTTP latency and status counts.

The bundled Grafana dashboard (auto-provisioned) shows outcome mix, policy
version mix, top failing criteria, explanation quality, retrieval health and
audit health. Alert rules in `monitoring/prometheus/alert_rules.yml` cover the
things that actually matter here - a guardrail violation, an empty policy index,
audit writes failing - not just CPU.

## 11. Repository layout

```
app/
  main.py                FastAPI factory, lifespan, /metrics mount
  pipeline.py            orchestration: rules -> RAG -> Claude -> guardrails -> audit
  schemas.py             Pydantic contracts shared by every component
  rules/                 deterministic engine
    engine.py            evaluation, outcomes, capacity, decision hashing
    predicates.py        safe tri-state condition language (no eval)
    facts.py             derived facts: amortisation, DTI, LTV, buffers
    registry.py          versioned rule-pack registry and date resolution
    versions/            rules_v1_0.yaml, rules_v2_0.yaml
  rag/                   chunker, embeddings, store, ingest, retriever
  extraction/            document parsers and the applicant field extractor
  llm/                   prompts, streaming Claude client, guardrails
  audit/                 SQLAlchemy models, engine, audit writer
  observability/         Prometheus metrics and request middleware
  routers/               health, policies, documents, eligibility, chat, audit
data/
  policies/v1.0, v2.0    synthetic DemoBank policy corpus (markdown)
  applicants/            synthetic applicant packs
ui/streamlit_app.py      Streamlit front end
scripts/                 ingest, end-to-end demo, policy integrity check
tests/ + tests/golden/   pytest suites and the regression cases
promptfoo/               pipeline provider, assertions, eval config
monitoring/              Prometheus config, alerts, Grafana provisioning
.github/workflows/ci.yml CI pipeline
docs/                    architecture, policy versioning, evaluation, runbook, safety
```

## 12. Capstone requirement map

| Requirement | Where |
| --- | --- |
| RAG | `app/rag/` (version-scoped retrieval, clause-level chunking) |
| Versioned eligibility rules | `app/rules/versions/*.yaml`, `app/rules/registry.py` |
| Deterministic rule engine | `app/rules/engine.py`, `app/rules/predicates.py` |
| Claude LLM | `app/llm/claude_client.py`, `app/llm/prompts.py` |
| FastAPI | `app/main.py`, `app/routers/` |
| Streaming responses | `app/pipeline.py` (SSE), `/eligibility/stream`, `/chat/stream` |
| Document ingestion | `app/extraction/parsers.py`, `app/rag/ingest.py` |
| Applicant information extraction | `app/extraction/applicant_extractor.py` |
| Citations | `app/rag/retriever.py`, `Citation` in `app/schemas.py` |
| Audit logging | `app/audit/` |
| Promptfoo evaluation | `promptfoo/` |
| CI/CD | `.github/workflows/ci.yml` |
| Docker | `Dockerfile`, `Dockerfile.ui`, `docker-compose.yml` |
| Prometheus | `app/observability/metrics.py`, `monitoring/prometheus/` |
| Grafana | `monitoring/grafana/` |
| Application monitoring | metrics + alert rules + audit summary endpoint |
| Regression testing | `tests/test_regression_golden.py`, `tests/golden/` |
| Policy versioning | `docs/POLICY_VERSIONING.md` and everything it links |

## 13. Known limitations

- Pattern-based extraction, not a document-understanding model: it is accurate
  and traceable on structured intake packs, and deliberately reports a field as
  missing rather than guessing. Scanned PDFs are out of scope (no OCR).
- Single-applicant assessment only; no joint applications, no affordability
  stress on secondary income.
- The guardrail check runs after generation. The decision is streamed first and
  a contradicting explanation is replaced, but a user watching the stream may
  briefly see text that is then corrected.
- SQLite audit storage and an in-container ChromaDB are demo choices; a real
  deployment needs a managed database, retention policy and access control.
- Prometheus and Grafana are unauthenticated and CORS is wide open - demo only.

## 14. Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - components, data flow, failure modes
- [docs/POLICY_VERSIONING.md](docs/POLICY_VERSIONING.md) - how to change a policy safely
- [docs/EVALUATION.md](docs/EVALUATION.md) - test strategy, regression and promptfoo
- [docs/RUNBOOK.md](docs/RUNBOOK.md) - operations, alerts, troubleshooting
- [docs/SAFETY_AND_DATA.md](docs/SAFETY_AND_DATA.md) - synthetic data, privacy, scope limits
