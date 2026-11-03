"""The evaluator. Pure: takes a flag payload, segments and a context, returns a value and why.

Precedence (first hit wins):

1. flag disabled (kill switch)            -> off variation, reason OFF
2. a prerequisite not met                 -> off variation, reason PREREQUISITE_FAILED
3. individual targets                     -> reason TARGET_MATCH
4. rules in order (clauses AND-ed)        -> reason RULE_MATCH (+ rule id / index)
5. fallthrough variation or rollout       -> reason FALLTHROUGH (+ in_rollout)

A rollout (on a rule or the fallthrough) may be an experiment with a `holdout` weight and
an optional `experiment` key. Held-out contexts get the off variation with `in_holdout`
in the reason (kind stays RULE_MATCH / FALLTHROUGH); see `bucketing` for the guarantees.

A prerequisite `{"flag_key", "variation"}` is met when that flag is on and evaluates, for
the same context, to that variation. Prerequisites are evaluated recursively against the
`flags` map; a missing prerequisite flag is simply not met.

Any structural problem (unknown variation, bad rollout, a prerequisite cycle or a chain
deeper than MAX_PREREQUISITE_DEPTH) yields the off variation with reason ERROR instead of
raising, so a mis-saved flag can never take down a caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from feature_flags.core.bucketing import holdout_seed, in_holdout, pick_variation
from feature_flags.core.clauses import Context, clause_matches

MAX_PREREQUISITE_DEPTH = 5  # how many levels of prerequisites a flag may sit on top of


@dataclass(frozen=True)
class Reason:
    kind: str
    rule_id: str | None = None
    rule_index: int | None = None
    in_rollout: bool = False
    in_holdout: bool = False
    experiment: str | None = None
    prerequisite_key: str | None = None
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


class PrerequisiteChainError(EvaluationError):
    """A prerequisite cycle or an over-deep chain. Reported as ERROR on the flag that was
    asked for rather than as a failed prerequisite somewhere down the chain."""


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


def _resolve(
    target: dict[str, Any], flag: dict[str, Any], context: Context
) -> tuple[str | None, dict[str, Any]]:
    """A rule or fallthrough points at a fixed variation or at a rollout. Returns the
    variation and the reason fields it implies (in_rollout / in_holdout / experiment)."""
    if target.get("rollout"):
        salt = flag.get("salt", "")
        bucket_by = target.get("bucket_by", "key")
        experiment = target.get("experiment")
        extra: dict[str, Any] = {"experiment": experiment} if experiment else {}
        seed = holdout_seed(flag["key"], salt, experiment)
        if in_holdout(int(target.get("holdout") or 0), seed, context, bucket_by):
            return flag.get("off_variation"), {**extra, "in_holdout": True}
        variation = pick_variation(target["rollout"], flag["key"], salt, context, bucket_by)
        return variation, {**extra, "in_rollout": True}
    if target.get("variation") is None:
        raise EvaluationError("rule has neither variation nor rollout")
    return target["variation"], {}


def _failed_prerequisite(
    flag: dict[str, Any],
    context: Context,
    segments: dict[str, dict],
    flags: dict[str, dict[str, Any]],
    chain: tuple[str, ...],
) -> str | None:
    """Key of the first prerequisite that is not met, or None when all are."""
    path = (*chain, flag["key"])
    for prerequisite in flag.get("prerequisites", []):
        key = prerequisite["flag_key"]
        if key in path:
            raise PrerequisiteChainError(f"prerequisite cycle: {' -> '.join((*path, key))}")
        if len(path) > MAX_PREREQUISITE_DEPTH:
            raise PrerequisiteChainError(
                f"prerequisites nested deeper than {MAX_PREREQUISITE_DEPTH} levels"
            )
        required = flags.get(key)
        if required is None:
            return key
        outcome = _evaluate(required, context, segments, flags, path)
        if (
            outcome.reason.kind in ("OFF", "ERROR")
            or outcome.variation != prerequisite["variation"]
        ):
            return key
    return None


def evaluate(
    flag: dict[str, Any],
    context: Context,
    segments: dict[str, dict] | None = None,
    flags: dict[str, dict[str, Any]] | None = None,
) -> EvalResult:
    """Evaluate one flag. `flags` is the rest of the payload (every flag by key), which flags
    that depend on other flags need; callers holding a full payload should always pass it."""
    return _evaluate(flag, context, segments or {}, flags or {}, ())


def _evaluate(
    flag: dict[str, Any],
    context: Context,
    segments: dict[str, dict],
    flags: dict[str, dict[str, Any]],
    chain: tuple[str, ...],
) -> EvalResult:
    version = int(flag.get("version", 0))
    off = flag.get("off_variation")

    def result(variation: str | None, reason: Reason) -> EvalResult:
        value = _variation_value(flag, variation) if variation is not None else None
        return EvalResult(flag["key"], variation, value, reason, version)

    try:
        if not flag.get("enabled", False):
            return result(off, Reason("OFF"))

        failed = _failed_prerequisite(flag, context, segments, flags, chain)
        if failed is not None:
            return result(off, Reason("PREREQUISITE_FAILED", prerequisite_key=failed))

        for target in flag.get("targets", []):
            if context.key in target.get("keys", []):
                return result(target["variation"], Reason("TARGET_MATCH"))

        for index, rule in enumerate(flag.get("rules", [])):
            clauses = rule.get("clauses", [])
            if clauses and all(_clause_holds(c, context, segments) for c in clauses):
                variation, extra = _resolve(rule, flag, context)
                return result(
                    variation,
                    Reason("RULE_MATCH", rule_id=rule.get("id"), rule_index=index, **extra),
                )

        fallthrough = flag.get("fallthrough") or {"variation": off}
        variation, extra = _resolve(fallthrough, flag, context)
        return result(variation, Reason("FALLTHROUGH", **extra))
    except (EvaluationError, KeyError, TypeError, ValueError) as exc:
        if chain and isinstance(exc, PrerequisiteChainError):
            raise  # surface on the flag that was asked for, not on a prerequisite
        try:
            return result(off, Reason("ERROR", error=str(exc)))
        except EvaluationError:
            return EvalResult(flag["key"], None, None, Reason("ERROR", error=str(exc)), version)


def evaluate_all(
    flags: dict[str, dict[str, Any]], context: Context, segments: dict[str, dict] | None = None
) -> dict[str, EvalResult]:
    return {key: evaluate(flag, context, segments, flags) for key, flag in flags.items()}
