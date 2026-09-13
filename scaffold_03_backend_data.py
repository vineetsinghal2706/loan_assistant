#!/usr/bin/env python3
"""Scaffolds backend/data: the two versioned personal-loan policy documents
(v1 and the v2 policy update that supersedes parts of it), the matching
versioned eligibility rule sets, and a labelled evaluation set used both by
promptfoo and by the standalone regression-gate script."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

FILES["backend/data/policies/personal_loan_policy_v1.md"] = r'''---
doc_id: personal_loan_policy
version: v1
effective_date: 2025-01-01
supersedes: null
---
## 1.2 Eligibility Age
Applicants must be between 21 and 60 years of age (inclusive) at the time of
application. Applicants outside this range are not eligible for a personal
loan under this policy.

## 1.4 Employment
Applicants must be either salaried or self-employed. Applicants who are
unemployed at the time of application do not meet the minimum employment
requirement for a personal loan.

## 2.1 Income Requirements
Applicants must have a minimum net monthly income of Rs 20,000 to qualify for
a personal loan, regardless of employment type.

## 2.3 Debt-to-Income
An applicant's existing monthly debt obligations, divided by their net
monthly income, must not exceed 50 percent (debt-to-income ratio, or DTI).
Applications above this threshold are declined.

## 3.1 Credit Score
A minimum credit score of 620 is required. Applicants scoring between 600 and
620 may be referred to manual underwriting review at the branch manager's
discretion.

## 4.1 Loan Amount and Tenure
Personal loans are available from Rs 50,000 up to Rs 15,00,000, with tenures
between 12 and 84 months, subject to income-based affordability checks.
'''

FILES["backend/data/policies/personal_loan_policy_v2_update.md"] = r'''---
doc_id: personal_loan_policy
version: v2
effective_date: 2026-06-01
supersedes: v1
---
## 2.1 Income Requirements
Effective 2026-06-01, this section is updated: applicants must have a minimum
net monthly income of Rs 25,000 to qualify for a personal loan, regardless of
employment type. This supersedes the Rs 20,000 threshold in the v1 policy and
reflects revised affordability guidelines.

## 2.3 Debt-to-Income
Updated in v2: the maximum permitted debt-to-income (DTI) ratio is revised
down to 45 percent (from 50 percent in v1). Applications with a DTI above 45
percent are declined under the current policy.

## 3.1 Credit Score
Updated in v2: the minimum qualifying credit score is raised to 650.
Applicants scoring between 630 and 650 are routed to manual underwriting
review ("needs review") rather than an automatic decline, consistent with
the bank's revised risk appetite.

## 5.1 Policy Change Log
v2 (effective 2026-06-01) supersedes v1 (effective 2025-01-01). Updated
sections: 2.1 Income Requirements, 2.3 Debt-to-Income, 3.1 Credit Score.
Unchanged from v1: 1.2 Eligibility Age, 1.4 Employment, 4.1 Loan Amount and
Tenure.
'''

FILES["backend/data/eligibility_rules/rules_v1.yaml"] = r'''version: v1
effective_date: "2025-01-01"
supersedes: null
rules:
  - id: R-AGE-01
    description: "Applicant age must be between 21 and 60 (inclusive)"
    condition: "21 <= age <= 60"
    section: "Policy section 1.2 Eligibility Age"
  - id: R-EMP-01
    description: "Employment type must not be unemployed"
    condition: "employment_type != 'unemployed'"
    section: "Policy section 1.4 Employment"
  - id: R-INC-01
    description: "Minimum net monthly income of Rs 20,000"
    condition: "monthly_income >= 20000"
    section: "Policy section 2.1 Income Requirements"
  - id: R-DTI-01
    description: "Debt-to-income ratio must not exceed 50%"
    condition: "(monthly_debt / monthly_income) <= 0.50"
    section: "Policy section 2.3 Debt-to-Income"
  - id: R-CS-01
    description: "Minimum credit score of 620"
    condition: "credit_score >= 620"
    section: "Policy section 3.1 Credit Score"
'''

FILES["backend/data/eligibility_rules/rules_v2.yaml"] = r'''version: v2
effective_date: "2026-06-01"
supersedes: v1
rules:
  - id: R-AGE-01
    description: "Applicant age must be between 21 and 60 (inclusive)"
    condition: "21 <= age <= 60"
    section: "Policy section 1.2 Eligibility Age"
  - id: R-EMP-01
    description: "Employment type must not be unemployed"
    condition: "employment_type != 'unemployed'"
    section: "Policy section 1.4 Employment"
  - id: R-INC-01
    description: "Minimum net monthly income of Rs 25,000 (raised from Rs 20,000 in v1)"
    condition: "monthly_income >= 25000"
    section: "Policy section 2.1 Income Requirements"
  - id: R-DTI-01
    description: "Debt-to-income ratio must not exceed 45% (lowered from 50% in v1)"
    condition: "(monthly_debt / monthly_income) <= 0.45"
    section: "Policy section 2.3 Debt-to-Income"
  - id: R-CS-01
    description: "Minimum credit score of 650, soft floor at 630 for manual review (raised from 620 in v1)"
    condition: "credit_score >= 650"
    section: "Policy section 3.1 Credit Score"
'''

FILES["backend/data/eval/labelled_eligibility_set.jsonl"] = r'''{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 40000, "monthly_debt": 5000, "credit_score": 720, "age": 30, "employment_type": "salaried", "requested_amount": 200000, "loan_tenure_months": 36}, "expected_outcome": "eligible", "expected_rule_ids": [], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 15000, "monthly_debt": 2000, "credit_score": 700, "age": 28, "employment_type": "salaried", "requested_amount": 100000, "loan_tenure_months": 24}, "expected_outcome": "not_eligible", "expected_rule_ids": ["R-INC-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 30000, "monthly_debt": 20000, "credit_score": 700, "age": 35, "employment_type": "salaried", "requested_amount": 150000, "loan_tenure_months": 48}, "expected_outcome": "not_eligible", "expected_rule_ids": ["R-DTI-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 30000, "monthly_debt": 3000, "credit_score": 640, "age": 25, "employment_type": "salaried", "requested_amount": 100000, "loan_tenure_months": 36}, "expected_outcome": "needs_review", "expected_rule_ids": ["R-CS-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 30000, "monthly_debt": 3000, "credit_score": 500, "age": 30, "employment_type": "salaried", "requested_amount": 100000, "loan_tenure_months": 24}, "expected_outcome": "not_eligible", "expected_rule_ids": ["R-CS-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 30000, "monthly_debt": 2000, "credit_score": 700, "age": 19, "employment_type": "salaried", "requested_amount": 50000, "loan_tenure_months": 12}, "expected_outcome": "not_eligible", "expected_rule_ids": ["R-AGE-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 30000, "monthly_debt": 2000, "credit_score": 700, "age": 30, "employment_type": "unemployed", "requested_amount": 50000, "loan_tenure_months": 12}, "expected_outcome": "not_eligible", "expected_rule_ids": ["R-EMP-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 50000, "monthly_debt": 10000, "credit_score": 680, "age": 45, "employment_type": "self_employed", "requested_amount": 300000, "loan_tenure_months": 60}, "expected_outcome": "eligible", "expected_rule_ids": [], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 30000, "monthly_debt": 2000, "credit_score": 700, "age": 65, "employment_type": "salaried", "requested_amount": 50000, "loan_tenure_months": 12}, "expected_outcome": "not_eligible", "expected_rule_ids": ["R-AGE-01"], "expected_keywords": []}
{"question": "Am I eligible for a personal loan given my profile?", "applicant": {"monthly_income": 26000, "monthly_debt": 5000, "credit_score": 635, "age": 50, "employment_type": "salaried", "requested_amount": 100000, "loan_tenure_months": 24}, "expected_outcome": "needs_review", "expected_rule_ids": ["R-CS-01"], "expected_keywords": []}
{"question": "What is the minimum income requirement for a personal loan?", "applicant": null, "expected_outcome": null, "expected_rule_ids": [], "expected_keywords": ["25,000", "updated"]}
{"question": "Is there a minimum credit score to qualify?", "applicant": null, "expected_outcome": null, "expected_rule_ids": [], "expected_keywords": ["650", "Credit Score"]}
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
