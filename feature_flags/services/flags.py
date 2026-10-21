"""Flags and their per-environment configs: create, update, kill switch, archive."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.models import Environment, Flag, FlagConfig, Project, Segment
from feature_flags.services import audit, payloads, webhooks
from feature_flags.services.bus import ChangeEvent
from feature_flags.services.context import AppContext
from feature_flags.services.errors import Conflict, NotFound
from feature_flags.services.projects import bump_environment
from feature_flags.services.validation import validate_config, validate_key, validate_variations

CONFIG_FIELDS = ("enabled", "off_variation", "targets", "rules", "fallthrough")


def config_snapshot(config: FlagConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "off_variation": config.off_variation,
        "targets": config.targets,
        "rules": config.rules,
        "fallthrough": config.fallthrough,
        "version": config.version,
    }


def segment_keys(session: Session, project_id: str) -> set[str]:
    return set(session.scalars(select(Segment.key).where(Segment.project_id == project_id)))


def create_flag(
    session: Session,
    ctx: AppContext,
    project: Project,
    *,
    key: str,
    name: str,
    kind: str,
    variations: list[dict[str, Any]],
    now: datetime,
    actor: str,
    description: str | None = None,
    tags: list[str] | None = None,
    off_variation: str | None = None,
    default_variation: str | None = None,
) -> Flag:
    validate_key(key, "flag key")
    validate_variations(kind, variations)
    if session.scalar(select(Flag).where(Flag.project_id == project.id, Flag.key == key)):
        raise Conflict(f"flag {key!r} already exists", code="duplicate_key")
    keys = [v["key"] for v in variations]
    if kind == "boolean":
        off = off_variation or next(v["key"] for v in variations if v["value"] is False)
        default = default_variation or next(v["key"] for v in variations if v["value"] is True)
    else:
        off = off_variation or keys[-1]
        default = default_variation or keys[0]
    if off not in keys or default not in keys:
        raise NotFound("off_variation/default_variation must name a variation")

    flag = Flag(
        project_id=project.id,
        key=key,
        name=name,
        description=description,
        kind=kind,
        variations=variations,
        tags=tags or [],
        salt=secrets.token_hex(8),
        created_at=now,
        updated_at=now,
    )
    for env in project.environments:
        flag.configs.append(
            FlagConfig(
                environment_id=env.id,
                enabled=False,
                off_variation=off,
                targets=[],
                rules=[],
                fallthrough={"variation": default},
                version=1,
                updated_at=now,
            )
        )
        bump_environment(env)
        payloads.invalidate(ctx.kv, env)
    session.add(flag)
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="flag.created",
        entity_type="flag",
        entity_id=key,
        now=now,
        after={
            "key": key,
            "name": name,
            "kind": kind,
            "variations": variations,
            "tags": tags or [],
        },
    )
    for env in project.environments:
        ctx.bus.publish(ChangeEvent(env.id, env.version, "flag", key))
    return flag


def get_flag(session: Session, project: Project, key: str, include_archived: bool = True) -> Flag:
    flag = session.scalar(select(Flag).where(Flag.project_id == project.id, Flag.key == key))
    if flag is None or (flag.archived and not include_archived):
        raise NotFound(f"flag {key!r} not found")
    return flag


def list_flags(
    session: Session, project: Project, *, include_archived: bool = False, tag: str | None = None
) -> list[Flag]:
    stmt = select(Flag).where(Flag.project_id == project.id).order_by(Flag.key)
    if not include_archived:
        stmt = stmt.where(Flag.archived.is_(False))
    flags = list(session.scalars(stmt))
    if tag:
        flags = [f for f in flags if tag in (f.tags or [])]
    return flags


def get_config(flag: Flag, env: Environment) -> FlagConfig:
    for config in flag.configs:
        if config.environment_id == env.id:
            return config
    raise NotFound(f"flag {flag.key!r} has no config for environment {env.key!r}")


def update_metadata(
    session: Session, project: Project, flag: Flag, *, now: datetime, actor: str, **fields: Any
) -> Flag:
    before = {"name": flag.name, "description": flag.description, "tags": flag.tags}
    for name in ("name", "description", "tags"):
        if fields.get(name) is not None:
            setattr(flag, name, fields[name])
    flag.updated_at = now
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="flag.updated",
        entity_type="flag",
        entity_id=flag.key,
        now=now,
        before=before,
        after={"name": flag.name, "description": flag.description, "tags": flag.tags},
    )
    return flag


def update_config(
    session: Session,
    ctx: AppContext,
    project: Project,
    flag: Flag,
    env: Environment,
    changes: dict[str, Any],
    *,
    now: datetime,
    actor: str,
    action: str = "flag.config_updated",
) -> FlagConfig:
    """Apply a partial patch (any of CONFIG_FIELDS), validate the result, bump versions,
    invalidate the payload cache, write audit and notify webhooks and SSE subscribers."""
    config = get_config(flag, env)
    before = config_snapshot(config)
    merged = {k: before[k] for k in CONFIG_FIELDS}
    unknown = set(changes) - set(CONFIG_FIELDS)
    if unknown:
        raise NotFound(f"unknown config fields: {sorted(unknown)}")
    merged.update({k: v for k, v in changes.items() if v is not None})
    validate_config(merged, flag.variations, segment_keys(session, project.id))
    if all(merged[k] == before[k] for k in CONFIG_FIELDS):
        return config

    for k in CONFIG_FIELDS:
        setattr(config, k, merged[k])
    config.version += 1
    config.updated_at = now
    flag.updated_at = now
    bump_environment(env)
    payloads.invalidate(ctx.kv, env)
    session.flush()
    after = config_snapshot(config)
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action=action,
        entity_type="flag",
        entity_id=flag.key,
        environment_key=env.key,
        now=now,
        before=before,
        after=after,
    )
    webhooks.notify(
        session,
        ctx,
        project,
        event_type=action,
        payload={
            "flag": flag.key,
            "environment": env.key,
            "actor": actor,
            "before": before,
            "after": after,
        },
        now=now,
    )
    ctx.bus.publish(ChangeEvent(env.id, env.version, "flag", flag.key))
    return config


def set_enabled(session, ctx, project, flag, env, enabled: bool, *, now, actor) -> FlagConfig:
    action = "flag.restored" if enabled else "flag.killed"
    return update_config(
        session, ctx, project, flag, env, {"enabled": enabled}, now=now, actor=actor, action=action
    )


def archive_flag(
    session: Session, ctx: AppContext, project: Project, flag: Flag, *, now: datetime, actor: str
) -> Flag:
    if flag.archived:
        return flag
    flag.archived = True
    flag.updated_at = now
    for env in project.environments:
        bump_environment(env)
        payloads.invalidate(ctx.kv, env)
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="flag.archived",
        entity_type="flag",
        entity_id=flag.key,
        now=now,
        before={"archived": False},
        after={"archived": True},
    )
    for env in project.environments:
        ctx.bus.publish(ChangeEvent(env.id, env.version, "flag", flag.key))
    return flag
