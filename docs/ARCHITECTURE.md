# Architecture

> DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY

## 1. Why the LLM is not in the decision path

The naive design asks a model "is this customer eligible?". It fails for
reasons that are structural, not fixable with a better prompt:

| Property a credit decision needs | What an LLM-as-decider gives |
| --- | --- |
| Reproducible | Sampling, prompt drift and context order change the answer. |
| Attributable to a clause | The model may cite a clause it did not actually use. |
| Version-correct | Nothing stops it mixing thresholds from two policy versions. |
| Reviewable before release | A prompt change is not a reviewable policy change. |
| Explainable after the fact | "The model said so" is not an explanation. |

So the system splits the problem. The rule engine decides. RAG supplies the
text of the policy. Claude writes the explanation. Each is individually
testable, and only the third is stochastic.

## 2. Component responsibilities

```
app/
  extraction/   document -> structured applicant facts (+ provenance)
  rules/        applicant facts + policy version -> outcome           [deterministic]
  rag/          policy question + policy version -> clauses           [retrieval]
  llm/          outcome + clauses -> explanation                      [generative]
  audit/        everything above -> one immutable row
  observability/ everything above -> Prometheus
  routers/      HTTP surface
  pipeline.py   the only place that knows the order of the steps
```

`app/rules/` imports no web framework, no vector store and no LLM SDK. That
constraint is what makes the engine unit-testable and what stops "just ask the
model" creeping back in.

## 3. Request flow (`POST /chat/stream`)

```
1  middleware assigns a trace id                         -> X-Trace-Id
2  pipeline.evaluate()
     registry.resolve(policy_version | as_of_date | default)
     facts.derive_facts(applicant, product, assumptions)
     for each rule: predicates.evaluate_condition(...)     PASS/FAIL/UNKNOWN/N-A
     outcome mapping, capacity (CR-5.1), decision_hash
   -> SSE: trace, policy, decision                        (decision is now final)
3  pipeline.retrieve()
     for each criterion the decision relied on:
       retriever.clause(version, document_id, section)     metadata-filtered
     plus semantic hits for the applicant's question
   -> SSE: citations
4  explainer.stream()
     system prompt forbids re-deciding; user turn carries the decision,
     the failed/unknown criteria, the facts used and the numbered clauses
   -> SSE: token*                                          (Claude or template)
5  guardrails.validate_explanation()
     contradiction / commitment language / citation integrity / version drift
   -> SSE: guardrail  (on failure the deterministic summary replaces the text)
6  audit.record(); metrics updated
   -> SSE: audit, done
```

The ordering is a contract, asserted in `tests/test_api.py`: the decision and
its citations are always sent before the first generated token.

## 4. The condition language

Rules are data, not code. A criterion is a leaf comparison or a composite:

```yaml
- id: MG-5.1-LTV
  severity: HARD
  condition:
    any_of:
      - {field: ltv_ratio, op: lte, value: 0.85}
      - all_of:
          - {field: is_first_time_buyer, op: is_true}
          - {field: credit_score, op: gte, value: 720}
          - {field: ltv_ratio, op: lte, value: 0.90}
  policy_reference: {document_id: DB-MG, section: MG-5.1, quote: "..."}
```

Evaluation is tri-state and never uses `eval`:

| Composite | `false` if | `unknown` if | `true` if |
| --- | --- | --- | --- |
| `all_of` | any child false | no child false, some unknown | all children true |
| `any_of` | all children false | no child true, some unknown | any child true |
| `none_of` | any child true | no child true, some unknown | all children false |

`unknown` propagates to `INSUFFICIENT_INFORMATION` for mandatory criteria and
to `ELIGIBLE_WITH_CONDITIONS` for supporting ones. That single decision is what
keeps "we don't know yet" from being delivered as "you were declined".

## 5. Derived facts

`app/rules/facts.py` is the only place financial arithmetic happens:

- indicative repayment: standard annuity on the product's representative rate
  and the applicant's term (or the policy default term)
- stressed repayment: the same, with the rate raised by the policy's stress
  increment (v2.0 CR-3.3)
- `dti_ratio`, `stressed_dti_ratio`, `income_multiple_used`
- `ltv_ratio` from the collateral that matches the product (property for a
  mortgage, vehicle for an auto loan)
- `savings_buffer_multiple`, `reserves_multiple`

Every function returns `None` rather than raising when an input is missing, and
`round_facts()` rounds floats before hashing so `decision_hash` is stable
across platforms.

## 6. RAG design decisions

- **Chunk by clause, not by token window.** A citation must point at "CR-3.1",
  so `##` headings carrying a clause id are the chunk boundaries. Each chunk
  text is prefixed with `[DB-CR CR-3.1 title] (policy v2.0)` so a retrieved
  chunk is self-describing in the prompt.
- **One collection, metadata-filtered by version.** Both corpora live together;
  `policy_version` is a hard filter on every query. A v1.0 enquiry physically
  cannot be answered with a v2.0 clause.
- **Two retrieval modes.** Clause-targeted retrieval (document + section +
  version) guarantees the citation matches the criterion the engine used;
  semantic retrieval adds context for the applicant's free-text question.
- **Graceful degradation.** `sentence-transformers` -> deterministic hashing
  embedder; ChromaDB -> JSON-backed in-memory store; retrieval failure ->
  citations built from the clause quotes declared in the rule pack.

## 7. Guardrails

Checked after generation, before the explanation is stored:

| Check | Example caught |
| --- | --- |
| Contradiction | "unfortunately you are not eligible" on an `ELIGIBLE` decision |
| Decline framing of unknowns | "declined" on `INSUFFICIENT_INFORMATION` |
| Commitment language | "guaranteed", "we will lend", "your loan is approved" |
| Citation integrity | no markers at all, or a marker that was never supplied |
| Version drift | "under version 1.0" on a decision made under 2.0 |

On failure: emit a `guardrail` event with the violations, replace the text with
the deterministic summary, increment
`demobank_guardrail_violations_total`, and store the violations on the audit
row. The alert rule on that counter is deliberately set to fire on a single
occurrence.

## 8. Audit record

One row per outcome: trace id, pseudonymous applicant reference, a SHA-256
fingerprint of the applicant *facts* (name excluded), product, policy version,
outcome, `decision_hash`, engine version, every criterion with status/detail/
observed values, the citations, the facts relied upon, explanation mode, model,
token counts, latency, and any guardrail violations. Names, identity numbers
and raw documents are never stored (DOC-4.1).

Reproducibility test: re-running the same applicant facts against the same
policy version must produce the same `decision_hash` (DOC-5.1).

## 9. Failure modes

| Failure | Behaviour |
| --- | --- |
| No `ANTHROPIC_API_KEY` | deterministic template explanation; `explanation_mode=deterministic_template` |
| Claude fails mid-stream | notice emitted, deterministic summary appended, `llm_fallbacks_total` incremented |
| Vector store unavailable | citations fall back to rule-pack quotes; `rag_empty_retrievals_total` incremented |
| Policy version unknown | HTTP 404 before any evaluation |
| Product not in that version | HTTP 422 |
| Audit write fails | answer still served, `audit_write_errors_total` incremented, critical alert |
| Applicant field missing | `INSUFFICIENT_INFORMATION` plus the exact list of what is needed |

## 10. What a production version would need

Authentication and per-role authorisation; a managed database with retention and
legal hold; a policy release process with sign-off and a staged rollout; model
version pinning with A/B evaluation; PII detection on uploads; rate limiting;
per-tenant isolation; and a human-review queue for every
`ELIGIBLE_WITH_CONDITIONS` outcome.
