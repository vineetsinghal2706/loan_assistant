"""The condition language must be tri-state and never raise on missing data."""

from __future__ import annotations

import pytest

from app.rules.predicates import ConditionError, evaluate_condition, referenced_fields

FACTS = {
    "credit_score": 700,
    "age": 34,
    "employment_status": "FULL_TIME",
    "documents_verified": False,
    "has_prior_bankruptcy": None,
    "dti_ratio": 0.31,
}


@pytest.mark.parametrize(
    "condition,expected",
    [
        ({"field": "credit_score", "op": "gte", "value": 660}, True),
        ({"field": "credit_score", "op": "gte", "value": 720}, False),
        ({"field": "credit_score", "op": "lt", "value": 720}, True),
        ({"field": "employment_status", "op": "in", "value": ["FULL_TIME", "PART_TIME"]}, True),
        ({"field": "employment_status", "op": "not_in", "value": ["FULL_TIME"]}, False),
        ({"field": "age", "op": "between", "value": [21, 70]}, True),
        ({"field": "documents_verified", "op": "is_false"}, True),
        ({"field": "documents_verified", "op": "is_true"}, False),
        ({"field": "credit_score", "op": "exists"}, True),
        ({"field": "missing_thing", "op": "not_exists"}, True),
    ],
)
def test_leaf_operators(condition, expected):
    assert evaluate_condition(condition, FACTS).satisfied is expected


def test_missing_value_is_unknown_not_false():
    outcome = evaluate_condition({"field": "liquid_savings", "op": "gte", "value": 1000}, FACTS)
    assert outcome.satisfied is None
    assert outcome.missing_fields == ["liquid_savings"]
    assert "required" in outcome.detail


def test_all_of_prefers_failure_over_unknown():
    outcome = evaluate_condition(
        {
            "all_of": [
                {"field": "credit_score", "op": "gte", "value": 800},
                {"field": "liquid_savings", "op": "gte", "value": 1000},
            ]
        },
        FACTS,
    )
    assert outcome.satisfied is False


def test_all_of_is_unknown_when_only_unknowns_remain():
    outcome = evaluate_condition(
        {
            "all_of": [
                {"field": "credit_score", "op": "gte", "value": 660},
                {"field": "liquid_savings", "op": "gte", "value": 1000},
            ]
        },
        FACTS,
    )
    assert outcome.satisfied is None
    assert outcome.missing_fields == ["liquid_savings"]


def test_any_of_short_circuits_on_success():
    outcome = evaluate_condition(
        {
            "any_of": [
                {"field": "liquid_savings", "op": "gte", "value": 1000},
                {"field": "credit_score", "op": "gte", "value": 660},
            ]
        },
        FACTS,
    )
    assert outcome.satisfied is True


def test_any_of_is_unknown_when_no_branch_succeeds_but_one_is_unknown():
    outcome = evaluate_condition(
        {
            "any_of": [
                {"field": "has_prior_bankruptcy", "op": "is_false"},
                {"field": "years_since_bankruptcy", "op": "gte", "value": 5},
            ]
        },
        FACTS,
    )
    assert outcome.satisfied is None


def test_none_of_and_not():
    assert (
        evaluate_condition({"none_of": [{"field": "age", "op": "gt", "value": 70}]}, FACTS).satisfied
        is True
    )
    assert (
        evaluate_condition({"not": {"field": "age", "op": "gt", "value": 70}}, FACTS).satisfied
        is True
    )
    assert evaluate_condition({"not": {"field": "unknown_x", "op": "gt", "value": 1}}, FACTS).satisfied is None


def test_malformed_conditions_raise():
    with pytest.raises(ConditionError):
        evaluate_condition({"field": "age"}, FACTS)
    with pytest.raises(ConditionError):
        evaluate_condition({"field": "age", "op": "wat", "value": 1}, FACTS)
    with pytest.raises(ConditionError):
        evaluate_condition({"all_of": []}, FACTS)
    with pytest.raises(ConditionError):
        evaluate_condition({"field": "age", "op": "gte"}, FACTS)


def test_referenced_fields_walks_the_tree():
    names = referenced_fields(
        {
            "any_of": [
                {"field": "has_prior_bankruptcy", "op": "is_false"},
                {"all_of": [{"field": "years_since_bankruptcy", "op": "gte", "value": 4}]},
            ]
        }
    )
    assert names == ["has_prior_bankruptcy", "years_since_bankruptcy"]
