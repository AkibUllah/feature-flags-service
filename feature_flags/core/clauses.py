"""Clause matching: one attribute, one operator, a list of values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from feature_flags.core.semver import InvalidVersion, Version

OPERATORS = frozenset(
    {
        "eq",
        "neq",
        "in",
        "not_in",
        "contains",
        "starts_with",
        "ends_with",
        "regex",
        "gt",
        "gte",
        "lt",
        "lte",
        "semver_eq",
        "semver_gt",
        "semver_gte",
        "semver_lt",
        "semver_lte",
        "exists",
        "in_segment",
    }
)


@dataclass(frozen=True)
class Context:
    """Who is being evaluated: a stable key plus arbitrary attributes."""

    key: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def get(self, attribute: str) -> Any:
        if attribute == "key":
            return self.key
        return self.attributes.get(attribute)


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_version(value: Any) -> Version | None:
    if not isinstance(value, str):
        return None
    try:
        return Version.parse(value)
    except InvalidVersion:
        return None


def _match_single(op: str, actual: Any, values: list[Any]) -> bool:
    if op == "eq":
        return any(actual == v for v in values)
    if op == "neq":
        return all(actual != v for v in values)
    if op == "in":
        return actual in values
    if op == "not_in":
        return actual not in values
    if op in ("contains", "starts_with", "ends_with", "regex"):
        if not isinstance(actual, str):
            return False
        if op == "contains":
            return any(isinstance(v, str) and v in actual for v in values)
        if op == "starts_with":
            return any(isinstance(v, str) and actual.startswith(v) for v in values)
        if op == "ends_with":
            return any(isinstance(v, str) and actual.endswith(v) for v in values)
        for pattern in values:
            try:
                if isinstance(pattern, str) and re.search(pattern, actual):
                    return True
            except re.error:
                continue
        return False
    if op in ("gt", "gte", "lt", "lte"):
        number = _to_number(actual)
        if number is None:
            return False
        for v in values:
            other = _to_number(v)
            if other is None:
                continue
            if (
                (op == "gt" and number > other)
                or (op == "gte" and number >= other)
                or (op == "lt" and number < other)
                or (op == "lte" and number <= other)
            ):
                return True
        return False
    if op.startswith("semver_"):
        version = _to_version(actual)
        if version is None:
            return False
        for v in values:
            other = _to_version(v)
            if other is None:
                continue
            if (
                (op == "semver_eq" and version == other)
                or (op == "semver_gt" and version > other)
                or (op == "semver_gte" and version >= other)
                or (op == "semver_lt" and version < other)
                or (op == "semver_lte" and version <= other)
            ):
                return True
        return False
    raise ValueError(f"unsupported operator {op!r}")


def clause_matches(clause: dict[str, Any], context: Context) -> bool:
    """Evaluate a clause (`in_segment` is handled by the evaluator, not here).

    List-valued attributes match if any element matches. `negate` inverts the result.
    """
    op = clause["op"]
    if op not in OPERATORS or op == "in_segment":
        raise ValueError(f"unsupported operator {op!r}")
    actual = context.get(clause["attribute"])
    values = list(clause.get("values", []))
    if op == "exists":
        result = actual is not None
    elif actual is None:
        result = op in ("neq", "not_in")
    elif isinstance(actual, list):
        result = any(_match_single(op, item, values) for item in actual)
    else:
        result = _match_single(op, actual, values)
    return (not result) if clause.get("negate") else result
