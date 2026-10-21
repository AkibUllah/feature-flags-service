"""Server-side evaluation for SDK requests, with usage stats."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import EvalResult, evaluate
from feature_flags.models import Environment
from feature_flags.services import payloads, stats
from feature_flags.services.context import AppContext
from feature_flags.services.errors import NotFound


def evaluate_flags(
    session: Session,
    ctx: AppContext,
    env: Environment,
    context: Context,
    flag_keys: list[str] | None,
    now: datetime,
) -> list[EvalResult]:
    payload, _ = payloads.cached_payload(
        session, ctx.kv, env, ctx.settings.payload_cache_ttl_seconds
    )
    flags = payload["flags"]
    if flag_keys is None:
        keys = list(flags)
    else:
        missing = [k for k in flag_keys if k not in flags]
        if missing:
            raise NotFound(f"unknown flags: {missing}", code="unknown_flag")
        keys = flag_keys
    results = []
    for key in keys:
        result = evaluate(flags[key], context, payload["segments"])
        stats.record(ctx.kv, env.id, key, result.variation, result.reason.kind, now)
        results.append(result)
    return results
