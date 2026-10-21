"""Build, cache and version the per-environment SDK payload.

The payload is what server SDKs download: every non-archived flag's config for that
environment plus every segment of the project. Its ETag is a hash of the JSON, so identical
content from different code paths yields the same tag, and a bumped `Environment.version`
invalidates the cache key.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import evaluate_all
from feature_flags.kv import KV
from feature_flags.models import Environment, Flag, FlagConfig, Segment

CACHE_PREFIX = "payload"


def flag_payload(flag: Flag, config: FlagConfig) -> dict[str, Any]:
    return {
        "key": flag.key,
        "kind": flag.kind,
        "version": config.version,
        "enabled": config.enabled,
        "variations": flag.variations,
        "off_variation": config.off_variation,
        "targets": config.targets,
        "rules": config.rules,
        "fallthrough": config.fallthrough,
        "salt": flag.salt,
    }


def segment_payload(segment: Segment) -> dict[str, Any]:
    return {
        "key": segment.key,
        "version": segment.version,
        "included": segment.included,
        "excluded": segment.excluded,
        "rules": segment.rules,
    }


def build_payload(session: Session, env: Environment) -> dict[str, Any]:
    rows = session.execute(
        select(Flag, FlagConfig)
        .join(FlagConfig, FlagConfig.flag_id == Flag.id)
        .where(
            Flag.project_id == env.project_id,
            Flag.archived.is_(False),
            FlagConfig.environment_id == env.id,
        )
        .order_by(Flag.key)
    ).all()
    segments = session.scalars(
        select(Segment).where(Segment.project_id == env.project_id).order_by(Segment.key)
    ).all()
    return {
        "environment": env.key,
        "version": env.version,
        "flags": {flag.key: flag_payload(flag, config) for flag, config in rows},
        "segments": {segment.key: segment_payload(segment) for segment in segments},
    }


def etag_for(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f'"{digest[:32]}"'


def cached_payload(
    session: Session, kv: KV, env: Environment, ttl: int
) -> tuple[dict[str, Any], str]:
    key = f"{CACHE_PREFIX}:{env.id}:{env.version}"
    raw = kv.get(key)
    if raw is not None:
        payload = json.loads(raw)
    else:
        payload = build_payload(session, env)
        kv.set(key, json.dumps(payload, separators=(",", ":")), ttl)
    return payload, etag_for(payload)


def invalidate(kv: KV, env: Environment) -> None:
    kv.delete(*kv.keys(f"{CACHE_PREFIX}:{env.id}:*"))


def client_payload(payload: dict[str, Any], context: Context) -> dict[str, Any]:
    """Evaluated values for one context; what client-side SDK keys receive (no rules leak)."""
    results = evaluate_all(payload["flags"], context, payload["segments"])
    return {
        "environment": payload["environment"],
        "version": payload["version"],
        "context_key": context.key,
        "flags": {
            key: {
                "value": r.value,
                "variation": r.variation,
                "reason": r.reason.as_dict(),
                "version": r.version,
            }
            for key, r in results.items()
        },
    }


def client_etag(etag: str, context: Context) -> str:
    seed = json.dumps({"key": context.key, "attributes": context.attributes}, sort_keys=True)
    digest = hashlib.sha256((etag + seed).encode()).hexdigest()[:32]
    return f'"{digest}"'
