"""The evaluator. Pure: takes a flag payload, segments and a context, returns a value and why.

Precedence (first hit wins):

1. flag disabled (kill switch)            -> off variation, reason OFF
2. individual targets                     -> reason TARGET_MATCH
3. rules in order (clauses AND-ed)        -> reason RULE_MATCH (+ rule id / index)
4. fallthrough variation or rollout       -> reason FALLTHROUGH (+ in_rollout)

Any structural problem (unknown variation, bad rollout) yields the off variation with
reason ERROR instead of raising, so a mis-saved flag can never take down a caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from feature_flags.core.bucketing import pick_variation
from feature_flags.core.clauses import Context, clause_matches


@dataclass(frozen=True)
class Reason:
    kind: str
    rule_id: str | None = None
    rule_index: int | None = None
    in_rollout: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v is not False}


@dataclass(frozen=True)
class EvalResult:
    flag_key: str
    variation: str | None
    value: Any
    reason: Reason
    version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "flag_key": self.flag_key,
            "variation": self.variation,
            "value": self.value,
            "reason": self.reason.as_dict(),
            "version": self.version,
        }


class EvaluationError(Exception):
    pass


def _variation_value(flag: dict[str, Any], variation: str) -> Any:
    for entry in flag["variations"]:
        if entry["key"] == variation:
            return entry["value"]
    raise EvaluationError(f"variation {variation!r} does not exist")


def segment_matches(segment: dict[str, Any], context: Context) -> bool:
    if context.key in segment.get("excluded", []):
        return False
    if context.key in segment.get("included", []):
        return True
    for rule in segment.get("rules", []):
        clauses = rule.get("clauses", [])
        if clauses and all(clause_matches(c, context) for c in clauses):
            return True
    return False


def _clause_holds(clause: dict[str, Any], context: Context, segments: dict[str, dict]) -> bool:
    if clause["op"] == "in_segment":
        hit = any(
            key in segments and segment_matches(segments[key], context)
            for key in clause.get("values", [])
        )
        return (not hit) if clause.get("negate") else hit
    return clause_matches(clause, context)


def _resolve(target: dict[str, Any], flag: dict[str, Any], context: Context) -> tuple[str, bool]:
    """A rule or fallthrough points at a fixed variation or at a rollout."""
    if target.get("rollout"):
        rollout = target["rollout"]
        return (
            pick_variation(
                rollout, flag["key"], flag.get("salt", ""), context, target.get("bucket_by", "key")
            ),
            True,
        )
    if target.get("variation") is None:
        raise EvaluationError("rule has neither variation nor rollout")
    return target["variation"], False


def evaluate(
    flag: dict[str, Any], context: Context, segments: dict[str, dict] | None = None
) -> EvalResult:
    segments = segments or {}
    version = int(flag.get("version", 0))
    off = flag.get("off_variation")

    def result(variation: str | None, reason: Reason) -> EvalResult:
        value = _variation_value(flag, variation) if variation is not None else None
        return EvalResult(flag["key"], variation, value, reason, version)

    try:
        if not flag.get("enabled", False):
            return result(off, Reason("OFF"))

        for target in flag.get("targets", []):
            if context.key in target.get("keys", []):
                return result(target["variation"], Reason("TARGET_MATCH"))

        for index, rule in enumerate(flag.get("rules", [])):
            clauses = rule.get("clauses", [])
            if clauses and all(_clause_holds(c, context, segments) for c in clauses):
                variation, in_rollout = _resolve(rule, flag, context)
                return result(
                    variation,
                    Reason(
                        "RULE_MATCH",
                        rule_id=rule.get("id"),
                        rule_index=index,
                        in_rollout=in_rollout,
                    ),
                )

        fallthrough = flag.get("fallthrough") or {"variation": off}
        variation, in_rollout = _resolve(fallthrough, flag, context)
        return result(variation, Reason("FALLTHROUGH", in_rollout=in_rollout))
    except (EvaluationError, KeyError, TypeError, ValueError) as exc:
        try:
            return result(off, Reason("ERROR", error=str(exc)))
        except EvaluationError:
            return EvalResult(flag["key"], None, None, Reason("ERROR", error=str(exc)), version)


def evaluate_all(
    flags: dict[str, dict[str, Any]], context: Context, segments: dict[str, dict] | None = None
) -> dict[str, EvalResult]:
    return {key: evaluate(flag, context, segments) for key, flag in flags.items()}
