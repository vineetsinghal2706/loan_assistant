# Safety, data and scope

> DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY

## 1. Everything here is synthetic

| Artefact | Status |
| --- | --- |
| "DemoBank" | fictional institution invented for this project |
| Policy documents (v1.0, v2.0) | written from scratch for this project |
| Thresholds (scores, ratios, multiples, waiting periods) | invented, not benchmarked against any real lender |
| Applicant packs and personas | invented; names are fictional |
| Credit score range 300-900 | an invented internal scale |
| Representative interest rates | assessment assumptions for the demo, not offers |

No real customer data, no confidential bank document and no proprietary
eligibility rule was used. Every policy file and every applicant file carries
the marker `DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY`, the API
returns it on `/` and `/health`, every response carries an
`X-Synthetic-Data` header, and the disclaimer is included in every explanation.

## 2. This is not a credit decision and not advice

The assistant produces an **indicative pre-qualification** against a fictional
policy. It does not create an offer, it does not commit anyone to lend, and it
is not financial, legal or tax advice. The system prompt forbids commitment
language ("approved", "guaranteed", "we will lend") and product or investment
recommendations, and the guardrails fail the explanation if any appears.

If you adapt this project for anything real, the eligibility criteria, the
affordability arithmetic, the disclosures and the entire governance process must
be replaced by your own, reviewed by the people accountable for them.

## 3. Data minimisation in the demo

Implemented to mirror section DOC-4.1 of the synthetic documentation standard:

- Applicants are identified by a pseudonymous reference (`DEMO-A-1042`).
- The audit row stores a SHA-256 fingerprint of the applicant *facts* with the
  name excluded - enough to prove two enquiries were identical, not enough to
  reconstruct an identity.
- `full_name` is accepted by the extractor (documents contain it) but is never
  stored in the audit log, never sent in the explanation prompt, and a promptfoo
  assertion fails the explanation if the name is echoed back.
- Uploaded documents are parsed in memory and are not persisted.
- No identity document numbers, account numbers or card numbers are requested,
  extracted or stored; the extractor has no patterns for them.

## 4. Where the model is and is not trusted

| Trusted to | Not trusted to |
| --- | --- |
| Word the outcome in plain language | Determine the outcome |
| Explain which criteria applied | Decide which criteria apply |
| Quote the supplied clauses | Retrieve or choose clauses |
| Summarise what the applicant could change | State a threshold not present in the decision |
| Answer "what changed between versions?" from supplied text | Choose which version applies |

Enforced structurally: the engine runs before the model; the model receives the
final outcome, the criterion-by-criterion results and numbered clauses; the
decision is streamed to the client first; the generated text is validated
afterwards and replaced if it contradicts the record.

## 5. Handling of unknowns

A mandatory criterion that cannot be evaluated yields
`INSUFFICIENT_INFORMATION` with an explicit list of what is needed. This is
never presented as a decline - the wording is checked by a guardrail and by a
promptfoo assertion, because "we could not assess you" being heard as "you were
rejected" is a real harm in a credit context.

## 6. Fairness note

The synthetic criteria use age (a product range), residency status, employment
category, income, credit score, credit history depth, existing obligations,
collateral value, savings and prior insolvency. No protected attribute beyond
age and residency is present, and none is inferred. Age and residency ranges
are common product-eligibility criteria but are also exactly the kind of
criterion a real lender must justify legally.

This project does **not** include a fairness assessment. Fairness in lending is
a property of the criteria themselves, so it cannot be tested from inside a
system that faithfully executes whatever criteria it is given. A real programme
needs disparate-impact analysis of the rule pack, not of the code.

## 7. Security posture of the demo

Deliberately minimal, and unsuitable for exposure:

- no authentication or authorisation on any endpoint;
- CORS allows all origins;
- Prometheus and Grafana are unauthenticated (Grafana has anonymous viewer
  access enabled);
- the audit database is a local SQLite file with no encryption at rest;
- no rate limiting, no request size limit beyond an 8 MB upload cap;
- container images run as a non-root user and pin no package hashes.

Run it on localhost or a private network. Do not put real data in it.

## 8. Reporting a problem in this demo

Open an issue describing the applicant facts (synthetic only), the product, the
policy version, the expected and the actual outcome, and the `decision_hash`
from the response. That is enough to reproduce any decision exactly.
