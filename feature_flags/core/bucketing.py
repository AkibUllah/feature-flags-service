"""Sticky percentage rollouts.

A context is hashed together with the flag key and a per-flag salt into a point in [0, 1).
The same context always lands on the same point for a flag, so rollouts are sticky, and
different flags use different points so a user in the 10% of flag A is not automatically in
the 10% of flag B.

Experiments can hold out a share of the traffic. Holdout membership is hashed separately
from the variation split (seeded by the experiment key, or by the flag key and salt), so:

- changing the variation weights never moves anyone into or out of the holdout,
- users outside the holdout keep exactly the variation they had without one,
- growing the holdout from 5% to 10% keeps the original 5% held out,
- flags that share an experiment key hold out the same users.
"""

from __future__ import annotations

import hashlib
from typing import Any

from feature_flags.core.clauses import Context

WEIGHT_TOTAL = 100_000  # weights are in thousandths of a percent: 50_000 == 50%
_HASH_DIGITS = 15  # 60 bits of the digest: plenty of resolution, fits in a float exactly


def bucket(flag_key: str, salt: str, context: Context, bucket_by: str = "key") -> float:
    """Deterministic point in [0, 1) for (flag, salt, context attribute)."""
    value = context.get(bucket_by)
    if value is None:
        value = context.key
    digest = hashlib.sha256(f"{flag_key}.{salt}.{value}".encode()).hexdigest()
    return int(digest[:_HASH_DIGITS], 16) / 16**_HASH_DIGITS


def holdout_seed(flag_key: str, salt: str, experiment: str | None = None) -> str:
    return f"experiment:{experiment}" if experiment else f"flag:{flag_key}.{salt}"


def in_holdout(holdout: int, seed: str, context: Context, bucket_by: str = "key") -> bool:
    """True when the context falls in the first `holdout` weight units of the holdout space."""
    if holdout <= 0:
        return False
    return bucket("holdout", seed, context, bucket_by) * WEIGHT_TOTAL < holdout


def validate_holdout(holdout: Any) -> None:
    if isinstance(holdout, bool) or not isinstance(holdout, int):
        raise ValueError("holdout must be an integer weight")
    if not 0 <= holdout < WEIGHT_TOTAL:
        raise ValueError(f"holdout must be >= 0 and < {WEIGHT_TOTAL}, got {holdout}")


def validate_rollout(rollout: list[dict[str, Any]]) -> None:
    if not rollout:
        raise ValueError("rollout needs at least one bucket")
    total = sum(int(entry["weight"]) for entry in rollout)
    if any(int(entry["weight"]) < 0 for entry in rollout):
        raise ValueError("rollout weights must be >= 0")
    if total != WEIGHT_TOTAL:
        raise ValueError(f"rollout weights must sum to {WEIGHT_TOTAL}, got {total}")


def pick_variation(
    rollout: list[dict[str, Any]],
    flag_key: str,
    salt: str,
    context: Context,
    bucket_by: str = "key",
) -> str:
    """Walk the cumulative weights until the context's bucket point is covered."""
    point = bucket(flag_key, salt, context, bucket_by)
    cumulative = 0
    for entry in rollout:
        cumulative += int(entry["weight"])
        if point * WEIGHT_TOTAL < cumulative:
            return entry["variation"]
    return rollout[-1]["variation"]  # guards float rounding at the very top of the range
