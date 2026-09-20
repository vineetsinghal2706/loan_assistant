# Runbook

> DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY
> This is an educational demo. Nothing here is on call.

## 1. Start, stop, inspect

```bash
docker compose up --build -d      # whole stack
docker compose ps
docker compose logs -f api
docker compose down               # add -v to drop volumes (index + audit db)
```

Local:

```bash
uvicorn app.main:app --reload            # API
streamlit run ui/streamlit_app.py        # UI
```

## 2. First checks

```bash
curl -s localhost:8000/health | jq
curl -s localhost:8000/health/ready | jq
curl -s localhost:8000/policies/versions | jq '.[].policy_version'
curl -s localhost:8000/audit/summary | jq
curl -s localhost:8000/metrics | grep demobank_ | head -30
```

`/health` reports which backends are actually live - `llm_mode`
(`claude` vs `deterministic_template`), `embedding_backend`, `vector_backend`
and `indexed_chunks`. Most "the assistant is behaving oddly" reports are
answered by that one response.

## 3. Configuration reference

| Variable | Default | Effect |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | empty | empty => deterministic template explanations |
| `ENABLE_LLM` | true | false forces the template even when a key exists |
| `CLAUDE_MODEL` | claude-sonnet-4-5 | model used for explanations |
| `EMBEDDING_BACKEND` | auto | `auto` \| `sentence-transformers` \| `hash` |
| `VECTOR_BACKEND` | auto | `auto` \| `chroma` \| `memory` |
| `CHROMA_DIR` | ./var/chroma | index location |
| `RETRIEVAL_TOP_K` | 5 | semantic hits per query |
| `DEFAULT_POLICY_VERSION` | 2.0 | used when neither version nor date is given |
| `AUDIT_DB_URL` | sqlite:///./var/audit.db | audit database |
| `API_BASE_URL` | http://localhost:8000 | used by the Streamlit UI |

## 4. Alert response

### GuardrailViolationsDetected (critical)

An explanation contradicted the deterministic decision. The user already
received the correct outcome and the corrected text, so this is a quality
incident, not a wrong answer.

```bash
curl -s 'localhost:8000/audit/decisions?limit=50' \
  | jq '[.[] | select(.guardrail_violations | length > 0)]'
```

Then: read the violations, compare `explanation` against the decision, check
whether the prompt or the model version changed, and add the case to
`promptfoo/promptfooconfig.yaml` so it is caught before release next time. If
the rate is high, set `ENABLE_LLM=false` and restart: the assistant keeps
working on deterministic explanations.

### PolicyIndexEmpty (critical)

Citations are falling back to rule-pack quotes.

```bash
curl -s -X POST 'localhost:8000/policies/reindex?rebuild=true' | jq
curl -s localhost:8000/health | jq '.indexed_chunks, .vector_backend'
```

If `vector_backend` is `memory` in a container, ChromaDB failed to open its
directory - check the volume mount and permissions on `/app/var`.

### AuditWritesFailing (critical)

Decisions are being served without a record, which breaks DOC-5.1. Check disk
space and the `AUDIT_DB_URL` path, then `docker compose restart api`. Do not
leave the service answering without an audit trail.

### LlmFallbackRate (warning)

Claude is erroring. Check the container logs for the provider error, verify the
key and the model name, and confirm rate limits. Users still get correct
outcomes with template explanations meanwhile.

### RetrievalReturnedNothing (warning)

A cited clause is not in the index. Usually a rule pack citing a clause that was
renamed in the policy document. Run `python scripts/validate_policies.py` -
it names the offending criterion.

### SlowPreQualification / HighHttpErrorRate

Get the trace id from the response header `X-Trace-Id` (or the `trace` SSE
event) and grep the logs. The engine itself is sub-millisecond; latency is
almost always the model or the embedder.

## 5. Common problems

| Symptom | Cause | Fix |
| --- | --- | --- |
| `llm_mode: deterministic_template` unexpectedly | no key, or `ENABLE_LLM=false` | set both, restart |
| First start is slow | downloading all-MiniLM-L6-v2 | pre-warm, or set `EMBEDDING_BACKEND=hash` |
| `INSUFFICIENT_INFORMATION` for everything | extraction or intake regression | `GET /documents/samples` then extract a sample; check `demobank_extraction_fields_total{result="missing"}` |
| Wrong thresholds quoted | wrong version resolved | send an explicit `policy_version`; check `policy_version` in the response |
| UI cannot reach the API | `API_BASE_URL` | in Compose it must be `http://api:8000` |
| Streamlit shows stale health | 60s cache | rerun the page |
| `/policies/versions/x` 404 | unknown version | `GET /policies/versions` |

## 6. Routine tasks

**Change a threshold.** Never edit YAML alone: follow
[POLICY_VERSIONING.md](POLICY_VERSIONING.md) - new version directory, new rule
pack, `validate_policies.py`, re-index, regression diff reviewed in the pull
request.

**Rotate the API key.** Update `.env`, `docker compose up -d api`, confirm
`/health` reports `llm_mode: claude`.

**Export the audit log.**

```bash
docker compose exec api python -c "
from app.audit.logger import get_audit_logger
import json
rows = get_audit_logger().list_recent(limit=200)
print(json.dumps([r.model_dump(mode='json') for r in rows], indent=2))
" > audit_export.json
```

**Reset the demo.** `docker compose down -v && docker compose up --build -d`
drops the index and the audit database.

## 7. Escalation boundary (educational)

If the assistant produces an outcome that a credit officer disagrees with, the
rule pack and the written policy are the authority, not the explanation.
Reproduce with `/eligibility/evaluate` (no LLM in the path) and compare the
`decision_hash` with the audit row. If they match, the engine did what the
policy says, and the disagreement is a policy question. If they differ, the
applicant data differed - the audit row holds the facts that were used.
