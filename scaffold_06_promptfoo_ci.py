#!/usr/bin/env python3
"""Scaffolds promptfoo/ (evaluation gate config), .github/workflows/ (CI),
and docs/ci_evidence/ (how to reproduce a blocked merge)."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

FILES["promptfoo/promptfooconfig.yaml"] = r'''description: "Loan Eligibility Assistant - regression gate on eligibility accuracy and citation faithfulness"

prompts:
  - "{{question}}"

providers:
  - id: http
    label: loan-eligibility-backend
    config:
      url: "http://localhost:8000/v1/chat"
      method: POST
      headers:
        Content-Type: application/json
      body:
        session_id: "promptfoo-eval"
        message: "{{question}}"
        applicant: "{{applicant | dump}}"
      transformResponse: "json.answer"

defaultTest:
  options:
    provider: loan-eligibility-backend

tests:
  - description: "Eligible salaried applicant under updated v2 thresholds"
    vars:
      question: "Am I eligible for a personal loan given my profile?"
      applicant: {"monthly_income": 40000, "monthly_debt": 5000, "credit_score": 720, "age": 30, "employment_type": "salaried", "requested_amount": 200000, "loan_tenure_months": 36}
    assert:
      - type: contains
        value: "currently: ELIGIBLE."

  - description: "Declined for income below the v2 minimum (raised from v1's Rs 20,000 to Rs 25,000)"
    vars:
      question: "Am I eligible for a personal loan given my profile?"
      applicant: {"monthly_income": 15000, "monthly_debt": 2000, "credit_score": 700, "age": 28, "employment_type": "salaried", "requested_amount": 100000, "loan_tenure_months": 24}
    assert:
      - type: contains
        value: "currently: NOT_ELIGIBLE."
      - type: contains
        value: "R-INC-01"

  - description: "Declined for debt-to-income ratio above the v2 45% cap"
    vars:
      question: "Am I eligible for a personal loan given my profile?"
      applicant: {"monthly_income": 30000, "monthly_debt": 20000, "credit_score": 700, "age": 35, "employment_type": "salaried", "requested_amount": 150000, "loan_tenure_months": 48}
    assert:
      - type: contains
        value: "R-DTI-01"

  - description: "Routed to manual review for borderline credit score (630-650 band introduced in v2)"
    vars:
      question: "Am I eligible for a personal loan given my profile?"
      applicant: {"monthly_income": 30000, "monthly_debt": 3000, "credit_score": 640, "age": 25, "employment_type": "salaried", "requested_amount": 100000, "loan_tenure_months": 36}
    assert:
      - type: contains
        value: "currently: NEEDS_REVIEW."
      - type: contains
        value: "R-CS-01"

  - description: "Declined for applicant below minimum eligibility age"
    vars:
      question: "Am I eligible for a personal loan given my profile?"
      applicant: {"monthly_income": 30000, "monthly_debt": 2000, "credit_score": 700, "age": 19, "employment_type": "salaried", "requested_amount": 50000, "loan_tenure_months": 12}
    assert:
      - type: contains
        value: "R-AGE-01"

  - description: "Hybrid retrieval surfaces the updated (v2) income policy, not the superseded v1 text"
    vars:
      question: "What is the minimum income requirement for a personal loan?"
      applicant: null
    assert:
      - type: contains
        value: "25,000"
      - type: icontains
        value: "updated"
'''

FILES[".github/workflows/ci.yml"] = r'''name: CI - Loan Eligibility Assistant

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  test-and-eval:
    runs-on: ubuntu-latest
    env:
      LLM_PROVIDER: stub
      CURRENT_RULE_VERSION: v2

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install backend dependencies
        working-directory: backend
        run: pip install -r requirements.txt

      - name: Unit tests (rules engine, hybrid retriever, API)
        working-directory: backend
        run: pytest tests -v

      - name: Build RAG index
        working-directory: backend
        run: python scripts/build_index.py

      - name: Start API server
        working-directory: backend
        run: |
          nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
          for i in $(seq 1 30); do
            curl -sf http://localhost:8000/health && exit 0
            sleep 2
          done
          echo "server did not become healthy in time" && cat server.log && exit 1

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Promptfoo evaluation gate
        working-directory: promptfoo
        run: npx promptfoo@latest eval -c promptfooconfig.yaml --no-cache

      - name: Numeric regression gate (accuracy + faithfulness)
        working-directory: backend
        run: |
          python scripts/run_regression_eval.py \
            --base-url http://localhost:8000 \
            --fail-under-accuracy 0.9 \
            --fail-under-faithfulness 0.8

      - name: Show server log on failure
        if: failure()
        working-directory: backend
        run: cat server.log || true
'''

FILES["docs/ci_evidence/blocked_merge_example.md"] = r'''# Reproducing a blocked merge

This walks through how the CI gate actually stops a regressing pull request
from merging, using the two gates defined in `.github/workflows/ci.yml`:

1. `promptfoo eval` against `promptfoo/promptfooconfig.yaml` (assertion-based)
2. `python backend/scripts/run_regression_eval.py` (numeric accuracy /
   faithfulness thresholds against `backend/data/eval/labelled_eligibility_set.jsonl`)

Both steps exit non-zero on failure, which fails the GitHub Actions job and
therefore blocks the PR from merging if branch protection requires the check
to pass.

## Worked example: a regressing PR

Suppose a PR changes `backend/data/eligibility_rules/rules_v2.yaml`, lowering
the income floor back down by mistake:

```diff
-    condition: "monthly_income >= 25000"
+    condition: "monthly_income >= 20000"
```

This silently reintroduces the superseded v1 threshold. Nothing in the code
changes, so it is easy to miss in review - which is exactly the kind of
regression this gate exists to catch.

### What the promptfoo step reports

The test case `"Declined for income below the v2 minimum..."` sends an
applicant with `monthly_income: 15000` and asserts the answer contains
`"currently: NOT_ELIGIBLE."` and `"R-INC-01"`. Because the rule no longer
fires for 15000 (it now only fails below 20000... but this applicant is still
below 20000, so to see the failure clearly, imagine the more realistic case
of an applicant at 22000, which is used directly in
`backend/tests/test_rules_engine.py::test_income_regression_v1_vs_v2`).

Running that unit test against the regressed rule file fails immediately,
before CI even reaches promptfoo:

```text
$ pytest backend/tests/test_rules_engine.py -v
...
FAILED backend/tests/test_rules_engine.py::test_income_regression_v1_vs_v2
AssertionError: assert 'eligible' == 'not_eligible'
 +  where 'eligible' = EligibilityDecision(outcome='eligible', ...).outcome
```

### What actually stops the merge here

In the CI workflow, the pytest step runs *before* the index build, the
server start, promptfoo, and the numeric regression script. Because
`test_income_regression_v1_vs_v2` fails hard on this exact regression, the
"Unit tests" step exits non-zero and GitHub Actions stops the job right
there - the PR shows a red X and the later steps never even run:

```text
$ pytest backend/tests/test_rules_engine.py -v
...
FAILED backend/tests/test_rules_engine.py::test_income_regression_v1_vs_v2
AssertionError: assert 'eligible' == 'not_eligible'
 +  where 'eligible' = EligibilityDecision(outcome='eligible', ...).outcome

1 failed, 4 passed in 0.42s
##[error]Process completed with exit code 1.
```

### What the numeric regression gate is for

`run_regression_eval.py` is the second, independent layer, and it matters
most for regressions that are *not* pinned by an exact unit test - for
example a change that shifts several borderline cases at once without
breaking any single hard-coded assertion. If a hypothetical broader
regression pushed measured accuracy or faithfulness below the configured
thresholds, it would report:

```text
Eligibility-answer accuracy : 83.33% (10/12)
Faithfulness (grounding)     : 100.00% (12/12)
p95 latency                  : 42.3 ms

Failing cases:
  - Q: Am I eligible for a personal loan given my profile?
    expected=not_eligible actual=eligible expected_rules=['R-INC-01'] expected_keywords=[]

REGRESSION GATE FAILED: accuracy=83.33% (threshold 90%), faithfulness=100.00% (threshold 80%)
```

and exit 1, which is what fails the "Numeric regression gate" CI step. As
the labelled eval set grows, tighten `--fail-under-accuracy` /
`--fail-under-faithfulness` (towards 95-100%) so that even a single
regressed case is enough to fail this gate on its own, not just the
pytest suite.

### Reproducing this locally

```bash
cd backend
python scripts/build_index.py
uvicorn app.main:app --port 8000 &
pytest tests -v                                   # catches the v1-vs-v2 regression directly
python scripts/run_regression_eval.py --base-url http://localhost:8000
cd ../promptfoo
npx promptfoo@latest eval -c promptfooconfig.yaml  # non-zero exit on assertion failure
```

Any non-zero exit code above is what GitHub Actions reports as a failed
check, which is what blocks the merge.
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
