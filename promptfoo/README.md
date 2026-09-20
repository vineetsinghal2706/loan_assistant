# Promptfoo evaluation

Educational demonstration. Synthetic DemoBank data only.

## What is being evaluated

Not a prompt in isolation - the whole explanation path:

```
golden applicant -> deterministic rule engine -> version-scoped retrieval -> explainer -> assertions
```

The deterministic engine gives every test case a known-correct outcome, so the
assertions can check the *explanation* against ground truth rather than against
a fuzzy expectation. The suite therefore answers a question that a plain LLM
eval cannot: **does the explanation stay faithful to the decision the bank
actually made?**

## Running

```bash
# offline: evaluates the deterministic template explainer
npx promptfoo@latest eval -c promptfoo/promptfooconfig.yaml

# with Claude
export ANTHROPIC_API_KEY=sk-ant-...
npx promptfoo@latest eval -c promptfoo/promptfooconfig.yaml

# browse results
npx promptfoo@latest view
```

Python assertions import the application package, so run from the repository
root with the project dependencies installed.

## Assertions

| Assertion | What it protects |
| --- | --- |
| `does_not_contradict_the_decision` | The LLM may not reverse, hedge or re-decide the outcome. |
| `states_the_correct_outcome` | The four outcome categories are worded correctly; insufficient information is never a decline. |
| `cites_the_applied_policy_version` | The explanation names the version applied and no other. |
| `names_the_driving_criteria` | Failed and unknown criteria are explained, not glossed over. |
| `uses_only_supplied_citations` | Citation markers exist and none are invented. |
| `stays_within_scope` | No commitment language, no investment advice, no identity leakage. |

## Test groups

1. **Positive outcomes** - eligible and conditional wording.
2. **Version-sensitive outcomes** - the same applicant across v1.0 and v2.0;
   the explanation must quote the threshold of the version applied.
3. **Negative and unknown outcomes** - including the case that must never be
   presented as a decline.
4. **Adversarial prompts** - jailbreak attempts asking the assistant to ignore
   the rule engine, to apply the superseded thresholds, to give investment
   advice, or to echo personal data.

A failure here is a failure of the explanation layer. The decision itself is
covered by `tests/test_regression_golden.py`.
