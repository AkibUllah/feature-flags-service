"""Structural validation of targeting config before it is saved."""

from __future__ import annotations

import re
from typing import Any

from feature_flags.core.bucketing import validate_rollout
from feature_flags.core.clauses import OPERATORS
from feature_flags.services.errors import DomainError

KINDS = frozenset({"boolean", "string", "number", "json"})
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


def validate_key(key: str, what: str = "key") -> None:
    if not KEY_PATTERN.match(key):
        raise DomainError(f"{what} must match {KEY_PATTERN.pattern}", code="invalid_key")


def validate_variations(kind: str, variations: list[dict[str, Any]]) -> None:
    if kind not in KINDS:
        raise DomainError(f"kind must be one of {sorted(KINDS)}", code="invalid_kind")
    if len(variations) < 2:
        raise DomainError("a flag needs at least two variations")
    keys = [v.get("key") for v in variations]
    if len(set(keys)) != len(keys) or any(not isinstance(k, str) or not k for k in keys):
        raise DomainError("variation keys must be unique non-empty strings")
    for v in variations:
        value = v.get("value")
        if kind == "boolean" and not isinstance(value, bool):
            raise DomainError("boolean flag variations must have true/false values")
        if kind == "string" and not isinstance(value, str):
            raise DomainError("string flag variations must have string values")
        if kind == "number" and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise DomainError("number flag variations must have numeric values")
    if kind == "boolean" and sorted(bool(v["value"]) for v in variations) != [False, True]:
        raise DomainError("boolean flags need exactly one true and one false variation")


def _validate_clause(clause: dict[str, Any], segment_keys: set[str]) -> None:
    op = clause.get("op")
    if op not in OPERATORS:
        raise DomainError(f"unsupported operator {op!r}", code="invalid_operator")
    if not clause.get("attribute") and op != "in_segment":
        raise DomainError("clause needs an attribute")
    values = clause.get("values", [])
    if not isinstance(values, list):
        raise DomainError("clause values must be a list")
    if op == "exists":
        return
    if not values:
        raise DomainError(f"clause {op!r} needs at least one value")
    if op == "in_segment":
        unknown = set(values) - segment_keys
        if unknown:
            raise DomainError(f"unknown segments: {sorted(unknown)}", code="unknown_segment")
    if op == "regex":
        for pattern in values:
            try:
                re.compile(pattern)
            except (re.error, TypeError) as exc:
                raise DomainError(
                    f"invalid regex {pattern!r}: {exc}", code="invalid_regex"
                ) from exc


def _validate_target(target: dict[str, Any], variation_keys: set[str], what: str) -> None:
    has_variation = target.get("variation") is not None
    has_rollout = bool(target.get("rollout"))
    if has_variation == has_rollout:
        raise DomainError(f"{what} needs exactly one of 'variation' or 'rollout'")
    if has_variation and target["variation"] not in variation_keys:
        raise DomainError(
            f"{what} points at unknown variation {target['variation']!r}", code="unknown_variation"
        )
    if has_rollout:
        try:
            validate_rollout(target["rollout"])
        except (ValueError, KeyError, TypeError) as exc:
            raise DomainError(f"{what} rollout invalid: {exc}", code="invalid_rollout") from exc
        for entry in target["rollout"]:
            if entry.get("variation") not in variation_keys:
                raise DomainError(
                    f"{what} rollout points at unknown variation", code="unknown_variation"
                )


def validate_config(
    config: dict[str, Any], variations: list[dict[str, Any]], segment_keys: set[str]
) -> None:
    variation_keys = {v["key"] for v in variations}
    if config.get("off_variation") not in variation_keys:
        raise DomainError("off_variation must name an existing variation", code="unknown_variation")
    for target in config.get("targets", []):
        if target.get("variation") not in variation_keys:
            raise DomainError("target points at unknown variation", code="unknown_variation")
        if not isinstance(target.get("keys"), list):
            raise DomainError("target keys must be a list")
    seen_ids: set[str] = set()
    for index, rule in enumerate(config.get("rules", [])):
        rule_id = rule.get("id")
        if not rule_id or rule_id in seen_ids:
            raise DomainError(f"rule #{index} needs a unique id")
        seen_ids.add(rule_id)
        clauses = rule.get("clauses", [])
        if not clauses:
            raise DomainError(f"rule {rule_id!r} needs at least one clause")
        for clause in clauses:
            _validate_clause(clause, segment_keys)
        _validate_target(rule, variation_keys, f"rule {rule_id!r}")
    _validate_target(config.get("fallthrough") or {}, variation_keys, "fallthrough")


def validate_segment_rules(rules: list[dict[str, Any]]) -> None:
    for index, rule in enumerate(rules):
        clauses = rule.get("clauses", [])
        if not clauses:
            raise DomainError(f"segment rule #{index} needs at least one clause")
        for clause in clauses:
            if clause.get("op") == "in_segment":
                raise DomainError("segments cannot reference other segments")
            _validate_clause(clause, set())
