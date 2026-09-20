# Evaluation strategy

> DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY

Because the decision is deterministic and the explanation is generative, the two
need different kinds of testing. Mixing them is the usual mistake.

| Layer | Question | Technique | Pass criterion |
| --- | --- | --- | --- |
| Facts | Is the arithmetic right? | unit tests against the closed-form annuity | exact within tolerance |
| Predicates | Is missing data handled as unknown? | tri-state truth tables | exact |
| Rule engine | Is the outcome correct for this applicant and version? | golden regression cases | exact |
| Versioning | Does the right policy apply? | date resolution + cross-version diffs | exact |
| RAG | Is the right clause retrieved, from the right version? | clause-targeted assertions | exact on version and section |
| Extraction | Are fields right, and are gaps admitted? | per-field assertions + provenance | exact, missing reported |
| Explanation | Is the text faithful to the decision? | guardrails + promptfoo | no contradiction, citations valid |
| API | Is the streaming contract honoured? | SSE event-order assertions | decision precedes tokens |

## 1. Golden regression suite

`tests/golden/regression_cases.json` holds 11 hand-authored applicants, each
with the expected outcome **and** the expected set of failed mandatory,
failed supporting and unknown criteria, for **both** policy versions - 22
expectations in total.

| Case | What it pins down |
| --- | --- |
| A clean personal loan | the happy path stays happy in both versions |
| B credit score 645 | v1.0 640 vs v2.0 660 |
| C tenure 9 months | v1.0 6 months vs v2.0 12 months |
| D DTI breach | affordability fails in both versions |
| E missing credit score | must be `INSUFFICIENT_INFORMATION`, never a decline |
| F thin file, unverified | supporting failures only => conditional |
| G mortgage 90% LTV, score 725 | the v2.0 first-time buyer concession applies |
| H mortgage 90% LTV, score 700 | the concession does not apply => v2.0 fails |
| I auto loan | tightened v2.0 LTV and term still satisfied |
| J short-term visa | product exclusion in both versions |
| K bankruptcy discharged 4.5 years ago | v2.0 relief: fails v1.0, passes v2.0 |

The suite also asserts:

- `test_golden_decisions_are_reproducible` - evaluating twice gives the same
  `decision_hash`;
- `test_version_sensitive_cases_actually_differ` - at least three cases must
  differ across versions, so the suite cannot decay into version-blindness;
- `test_named_criteria_are_the_reason` - the *reason* is checked, not just the
  outcome, so a case cannot pass for the wrong reason.

**How to treat a regression failure.** Either the rule pack changed
deliberately - in which case update the golden file in the same pull request and
explain the portfolio impact in the description - or it is a bug. There is no
third option, and "update the golden file to make CI green" without a policy
justification is exactly the thing this suite exists to prevent.

## 2. RAG evaluation

Retrieval is evaluated on the property that matters here, not on a generic
relevance score:

- every chunk returned under a version filter belongs to that version;
- a clause-targeted lookup returns that exact clause
  (`clause("2.0", "DB-CR", "CR-2.1")` contains 660, the v1.0 lookup contains 640);
- every citation attached to a decision carries the version that was applied;
- citation markers are contiguous from `[1]`, so the explanation cannot cite a
  marker that does not exist.

The deterministic hashing embedder makes these tests reproducible with no model
download; the same assertions hold with `sentence-transformers`, which is why
the embedder is swappable behind one interface.

## 3. Explanation evaluation (promptfoo)

`promptfoo/promptfooconfig.yaml` runs a **custom provider** that executes the
whole pipeline rather than a bare prompt, so ground truth for every test case
comes from the rule engine. Assertions then compare the generated text against
that ground truth:

| Assertion | Failure it catches |
| --- | --- |
| `does_not_contradict_the_decision` | the model reverses or hedges the outcome |
| `states_the_correct_outcome` | conditional presented as unconditional; unknown presented as a decline |
| `cites_the_applied_policy_version` | quoting v1.0 thresholds on a v2.0 decision |
| `names_the_driving_criteria` | "you don't qualify" with no reason given |
| `uses_only_supplied_citations` | invented citation markers |
| `stays_within_scope` | commitment language, investment advice, name echoing |

Four adversarial cases attempt to break the boundary: override the rule engine,
apply the superseded thresholds, extract investment advice, and echo personal
data. They must fail to move the outcome.

The suite passes in two configurations - with `ANTHROPIC_API_KEY` (Claude) and
without it (deterministic template) - so CI proves the guardrails hold for the
model *and* that the fallback is itself compliant.

## 4. What is deliberately not evaluated with an LLM judge

Outcome correctness. A model graded by another model is not an acceptable basis
for a credit answer. Judges are useful for style and clarity; here the
substantive assertions are all programmatic, and the only subjective dimension
left to promptfoo's optional rubrics would be readability.

## 5. Running everything

```bash
make validate      # rule packs vs written policy
make test          # all pytest suites, offline backends
make regression    # golden decisions + versioning
make eval          # promptfoo
```

CI runs these as separate jobs so a failure names the layer that broke:
`lint -> policy-integrity -> unit-tests -> {regression, promptfoo, docker}`.

## 6. Coverage gaps worth being honest about

- No load or soak testing; latency targets are asserted only as alert rules.
- No fairness or disparate-impact analysis. The synthetic corpus contains no
  protected attributes, and the rule engine never receives any, but a real
  deployment would need an explicit fairness review of the *criteria
  themselves*.
- Extraction is tested on structured intake packs, not on messy real-world
  documents or scans.
- The guardrail regexes catch the failure modes seen in this design; they are a
  safety net, not a proof of faithfulness.
