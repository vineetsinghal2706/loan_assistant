# Policy versioning

> DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY

## 1. What is versioned

Three things move together, and all three carry the version:

| Artefact | Location | Version marker |
| --- | --- | --- |
| Written policy (what a human reads) | `data/policies/v{X.Y}/*.md` | directory + front matter |
| Rule pack (what the engine executes) | `app/rules/versions/rules_v{X_Y}.yaml` | `policy_version` key |
| Indexed clauses (what RAG retrieves) | vector store metadata | `policy_version` on every chunk |

A citation therefore names a document, a clause and a version, and the audit row
stores the version that was applied. There is no way to answer a v1.0 enquiry
with a v2.0 clause: retrieval filters on the metadata field.

## 2. Resolution order

```
explicit policy_version   ->  use it (404 if unknown)
as_of_date                ->  the version whose effective window contains that date
neither                   ->  DEFAULT_POLICY_VERSION, else the highest active version
```

Implemented in `PolicyRegistry.resolve()`; the branch taken is recorded in the
`resolution` label of `demobank_policy_version_applied_total`, so a dashboard
shows whether traffic is using the default or an explicit historic version.

## 3. The two registered versions

| | v1.0 | v2.0 |
| --- | --- | --- |
| Status | superseded | active |
| Effective | 2023-01-01 to 2025-06-30 | 2025-07-01 onwards |
| Criteria | 38 across three products | 42 across three products |

### Changes in v2.0

| Clause | v1.0 | v2.0 | Direction |
| --- | --- | --- | --- |
| CR-2.1 personal | 640 | 660 | tighter |
| CR-2.1 mortgage | 660 | 680 | tighter |
| CR-2.1 auto | 620 | 640 | tighter |
| CR-3.1 personal | 45% | 40% | tighter |
| CR-3.1 mortgage | 43% | 40% | tighter |
| CR-3.1 auto | 45% | 42% | tighter |
| CR-3.3 stressed affordability | - | +2.00 pp, max 50% (mandatory) | new |
| CR-4.1 bankruptcy discharge | 5 years | 4 years | **looser** |
| PL-3.2 employment tenure | 6 months | 12 months | tighter |
| PL-4.1 maximum amount | 50,000 | 60,000 | looser |
| PL-4.2 income multiple | 5.0x | 4.5x | tighter |
| PL-5.2 savings buffer | - | 3x obligations (supporting) | new |
| MG-4.2 income multiple | 5.5x | 5.0x | tighter |
| MG-5.1 maximum LTV | 90% | 85% | tighter |
| MG-5.4 first-time buyer concession | - | 90% LTV at score 720+ | new relief |
| MG-6.2 reserves | - | 3x repayment (supporting) | new |
| AL-4.2 maximum LTV | 90% | 85% | tighter |
| AL-4.3 maximum term | 84 months | 72 months | tighter |

The bankruptcy window is deliberately *looser* in v2.0: version comparison must
be able to show relief as well as tightening, or the feature only ever tells
applicants bad news.

## 4. Explaining a change to an applicant

`POST /eligibility/compare-versions` runs one applicant through every registered
version and returns a machine-readable diff:

```json
{
  "entries": [
    {"policy_version": "1.0", "outcome": "ELIGIBLE", "failed_hard_rules": []},
    {"policy_version": "2.0", "outcome": "NOT_ELIGIBLE",
     "failed_hard_rules": ["CR-2.1-CREDIT-SCORE"]}
  ],
  "differences": [
    "Outcome changed from ELIGIBLE under policy 1.0 to NOT_ELIGIBLE under policy 2.0.",
    "Criterion CR-2.1-CREDIT-SCORE (CR-2.1) moved from PASS to FAIL between policy 1.0 and 2.0."
  ]
}
```

The diff distinguishes three kinds of change: an outcome change, a criterion
whose status moved, and a criterion that was added or removed between versions.

## 5. How to add policy version 3.0

1. **Write the policy.** Copy `data/policies/v2.0/` to `data/policies/v3.0/`,
   edit the clauses, and update the front matter (`policy_version`, `status`,
   `effective_from`, `supersedes`). Add a `summary of changes` section - it is
   what the assistant retrieves when an applicant asks "what changed?".
2. **Close the previous window.** Set `effective_to` on v2.0 in both its
   documents and its rule pack, and change its `status` to `superseded`.
3. **Encode the rules.** Copy `rules_v2_0.yaml` to `rules_v3_0.yaml`, bump
   `policy_version`, set the dates, fill in `change_log` (the API publishes it),
   and edit the criteria. Every rule needs a `policy_reference` pointing at a
   clause that exists in v3.0.
4. **Check integrity.** `python scripts/validate_policies.py` - fails if a rule
   cites a clause that does not exist, or cites the wrong version, and warns if
   the number a rule enforces is not written in the clause it cites.
5. **Re-index.** `python scripts/ingest_policies.py` (or `POST /policies/reindex`).
   Nothing needs to be deleted: versions coexist.
6. **Run the regression suite.** `make regression`. Golden cases only assert
   v1.0 and v2.0, so they must all still pass unchanged. Then extend
   `tests/golden/regression_cases.json` with the v3.0 expectations - reviewing
   that diff is how a human signs off on the policy change.
7. **Flip the default** with `DEFAULT_POLICY_VERSION=3.0` once the date arrives.
   Existing enquiries keep working: `as_of_date` still resolves to the version
   that was in force.

## 6. Rules that the versioning design enforces

- A rule may only cite its own version (`test_rule_packs_only_cite_their_own_version`).
- Effective windows must not overlap (`test_effective_windows_do_not_overlap`).
- Retrieval must not cross versions (`test_search_never_crosses_policy_versions`).
- A citation's threshold must match the version applied
  (`test_citations_for_v1_decision_quote_v1_thresholds`).
- At least three golden cases must produce different outcomes across versions,
  so the suite cannot quietly stop testing versioning
  (`test_version_sensitive_cases_actually_differ`).

## 7. Anti-patterns this design avoids

| Anti-pattern | Why it hurts | What is done instead |
| --- | --- | --- |
| Thresholds in Python | A policy change becomes a code change nobody in credit can review | YAML rule packs |
| One "current" policy folder | History is destroyed; you cannot answer "what was the answer in March?" | versions coexist |
| Version as a prompt instruction | The model may ignore it | metadata filter in the retriever |
| Re-indexing over the old version | Old citations become unresolvable | additive ingestion, keyed by version |
| Silent threshold edits | Nobody notices the portfolio impact | golden regression diff in the pull request |
