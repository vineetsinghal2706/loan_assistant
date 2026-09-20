"""A small, safe condition language for eligibility rules.

Rules are authored in YAML and never evaluated with ``eval``. A condition is
either a leaf comparison::

    {field: credit_score, op: gte, value: 660}

or a composite::

    {all_of: [...]}   {any_of: [...]}   {none_of: [...]}   {not: {...}}

Evaluation is tri-state:

* ``True``  - the condition is satisfied
* ``False`` - the condition is violated
* ``None``  - the condition cannot be evaluated because data is missing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

COMPOSITE_KEYS = ("all_of", "any_of", "none_of", "not")

LEAF_OPS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "between",
    "is_true",
    "is_false",
    "exists",
    "not_exists",
    "contains",
}

NO_VALUE_OPS = {"is_true", "is_false", "exists", "not_exists"}


class ConditionError(ValueError):
    """Raised when a rule pack contains a malformed condition."""


@dataclass
class ConditionOutcome:
    satisfied: Optional[bool]
    detail: str = ""
    missing_fields: List[str] = field(default_factory=list)
    observed: Dict[str, Any] = field(default_factory=dict)

    def merged(self, others: Sequence["ConditionOutcome"]) -> "ConditionOutcome":
        missing: List[str] = list(self.missing_fields)
        observed: Dict[str, Any] = dict(self.observed)
        for other in others:
            for name in other.missing_fields:
                if name not in missing:
                    missing.append(name)
            observed.update(other.observed)
        return ConditionOutcome(self.satisfied, self.detail, missing, observed)


def _format(value: Any) -> str:
    if value is None:
        return "not provided"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


OP_SYMBOLS = {
    "eq": "is",
    "ne": "is not",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "in": "one of",
    "not_in": "not one of",
    "between": "between",
    "contains": "contains",
}


def _compare(op: str, actual: Any, expected: Any) -> Optional[bool]:
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "in":
            return actual in expected
        if op == "not_in":
            return actual not in expected
        if op == "contains":
            return expected in actual
        if op == "between":
            low, high = expected
            return low <= actual <= high
    except TypeError:
        return None
    return None


def _evaluate_leaf(condition: Mapping[str, Any], facts: Mapping[str, Any]) -> ConditionOutcome:
    field_name = condition.get("field")
    op = condition.get("op")
    if not field_name or not op:
        raise ConditionError(f"Leaf condition requires 'field' and 'op': {condition!r}")
    if op not in LEAF_OPS:
        raise ConditionError(f"Unsupported operator '{op}' in condition {condition!r}")
    if op not in NO_VALUE_OPS and "value" not in condition:
        raise ConditionError(f"Operator '{op}' requires a 'value': {condition!r}")

    actual = facts.get(field_name)
    observed = {field_name: actual}
    label = condition.get("label", field_name)

    if op == "exists":
        satisfied = actual is not None
        return ConditionOutcome(
            satisfied, f"{label} {'provided' if satisfied else 'not provided'}", [], observed
        )
    if op == "not_exists":
        satisfied = actual is None
        return ConditionOutcome(
            satisfied, f"{label} {'not provided' if satisfied else 'provided'}", [], observed
        )

    if actual is None:
        return ConditionOutcome(
            None, f"{label} is required but was not provided", [str(field_name)], observed
        )

    if op == "is_true":
        return ConditionOutcome(bool(actual) is True, f"{label} is {_format(actual)}", [], observed)
    if op == "is_false":
        return ConditionOutcome(
            bool(actual) is False, f"{label} is {_format(actual)}", [], observed
        )

    expected = condition.get("value")
    satisfied = _compare(op, actual, expected)
    if satisfied is None:
        return ConditionOutcome(
            None,
            f"{label} ({_format(actual)}) could not be compared with {_format(expected)}",
            [str(field_name)],
            observed,
        )
    symbol = OP_SYMBOLS.get(op, op)
    detail = f"{label} is {_format(actual)}; required {symbol} {_format(expected)}"
    return ConditionOutcome(bool(satisfied), detail, [], observed)


def evaluate_condition(
    condition: Optional[Mapping[str, Any]], facts: Mapping[str, Any]
) -> ConditionOutcome:
    """Evaluate a condition tree against the derived facts."""

    if condition is None:
        return ConditionOutcome(True, "no condition", [], {})
    if not isinstance(condition, Mapping):
        raise ConditionError(f"Condition must be a mapping, got {type(condition)!r}")

    present = [key for key in COMPOSITE_KEYS if key in condition]
    if len(present) > 1:
        raise ConditionError(f"Condition has multiple composite keys: {present}")

    if not present:
        return _evaluate_leaf(condition, facts)

    key = present[0]

    if key == "not":
        inner = evaluate_condition(condition["not"], facts)
        satisfied = None if inner.satisfied is None else (not inner.satisfied)
        return ConditionOutcome(
            satisfied, f"not ({inner.detail})", list(inner.missing_fields), dict(inner.observed)
        )

    children_spec = condition[key]
    if not isinstance(children_spec, (list, tuple)) or not children_spec:
        raise ConditionError(f"'{key}' requires a non-empty list of conditions")
    children = [evaluate_condition(child, facts) for child in children_spec]

    failures = [child for child in children if child.satisfied is False]
    unknowns = [child for child in children if child.satisfied is None]
    successes = [child for child in children if child.satisfied is True]

    if key == "all_of":
        if failures:
            return ConditionOutcome(
                False, "; ".join(child.detail for child in failures)
            ).merged(children)
        if unknowns:
            return ConditionOutcome(
                None, "; ".join(child.detail for child in unknowns)
            ).merged(children)
        return ConditionOutcome(True, "; ".join(child.detail for child in children)).merged(
            children
        )

    if key == "any_of":
        if successes:
            return ConditionOutcome(
                True, " or ".join(child.detail for child in successes)
            ).merged(children)
        if unknowns:
            return ConditionOutcome(
                None, "; ".join(child.detail for child in unknowns)
            ).merged(children)
        return ConditionOutcome(
            False, " and ".join(child.detail for child in children)
        ).merged(children)

    # none_of
    if successes:
        return ConditionOutcome(
            False, "; ".join(child.detail for child in successes)
        ).merged(children)
    if unknowns:
        return ConditionOutcome(None, "; ".join(child.detail for child in unknowns)).merged(
            children
        )
    return ConditionOutcome(True, "; ".join(child.detail for child in children)).merged(children)


def referenced_fields(condition: Optional[Mapping[str, Any]]) -> List[str]:
    """Every fact name a condition depends on (used for documentation/tests)."""
    if condition is None:
        return []
    names: List[str] = []
    if isinstance(condition, Mapping):
        for key in COMPOSITE_KEYS:
            if key in condition:
                children = condition[key]
                if key == "not":
                    children = [children]
                for child in children:
                    for name in referenced_fields(child):
                        if name not in names:
                            names.append(name)
                return names
        name = condition.get("field")
        if name:
            names.append(str(name))
    return names
