from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.models import Project, Segment
from feature_flags.services import audit, payloads
from feature_flags.services.bus import ChangeEvent
from feature_flags.services.context import AppContext
from feature_flags.services.errors import Conflict, NotFound
from feature_flags.services.projects import bump_environment
from feature_flags.services.validation import validate_key, validate_segment_rules


def snapshot(segment: Segment) -> dict[str, Any]:
    return {
        "key": segment.key,
        "name": segment.name,
        "included": segment.included,
        "excluded": segment.excluded,
        "rules": segment.rules,
        "version": segment.version,
    }


def _touch_environments(ctx: AppContext, project: Project, key: str) -> None:
    for env in project.environments:
        bump_environment(env)
        payloads.invalidate(ctx.kv, env)
        ctx.bus.publish(ChangeEvent(env.id, env.version, "segment", key))


def create_segment(
    session: Session,
    ctx: AppContext,
    project: Project,
    *,
    key: str,
    name: str,
    now: datetime,
    actor: str,
    description: str | None = None,
    included: list[str] | None = None,
    excluded: list[str] | None = None,
    rules: list[dict[str, Any]] | None = None,
) -> Segment:
    validate_key(key, "segment key")
    validate_segment_rules(rules or [])
    if session.scalar(select(Segment).where(Segment.project_id == project.id, Segment.key == key)):
        raise Conflict(f"segment {key!r} already exists", code="duplicate_key")
    segment = Segment(
        project_id=project.id,
        key=key,
        name=name,
        description=description,
        included=included or [],
        excluded=excluded or [],
        rules=rules or [],
        created_at=now,
        updated_at=now,
    )
    session.add(segment)
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="segment.created",
        entity_type="segment",
        entity_id=key,
        now=now,
        after=snapshot(segment),
    )
    _touch_environments(ctx, project, key)
    return segment


def get_segment(session: Session, project: Project, key: str) -> Segment:
    segment = session.scalar(
        select(Segment).where(Segment.project_id == project.id, Segment.key == key)
    )
    if segment is None:
        raise NotFound(f"segment {key!r} not found")
    return segment


def list_segments(session: Session, project: Project) -> list[Segment]:
    return list(
        session.scalars(
            select(Segment).where(Segment.project_id == project.id).order_by(Segment.key)
        )
    )


def update_segment(
    session: Session,
    ctx: AppContext,
    project: Project,
    segment: Segment,
    changes: dict[str, Any],
    *,
    now: datetime,
    actor: str,
) -> Segment:
    before = snapshot(segment)
    if "rules" in changes and changes["rules"] is not None:
        validate_segment_rules(changes["rules"])
    for field in ("name", "description", "included", "excluded", "rules"):
        if changes.get(field) is not None:
            setattr(segment, field, changes[field])
    segment.version += 1
    segment.updated_at = now
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="segment.updated",
        entity_type="segment",
        entity_id=segment.key,
        now=now,
        before=before,
        after=snapshot(segment),
    )
    _touch_environments(ctx, project, segment.key)
    return segment


def delete_segment(
    session: Session,
    ctx: AppContext,
    project: Project,
    segment: Segment,
    *,
    now: datetime,
    actor: str,
) -> None:
    from feature_flags.models import Flag

    for flag in session.scalars(
        select(Flag).where(Flag.project_id == project.id, Flag.archived.is_(False))
    ):
        for config in flag.configs:
            for rule in config.rules:
                for clause in rule.get("clauses", []):
                    if clause.get("op") == "in_segment" and segment.key in clause.get("values", []):
                        raise Conflict(
                            f"segment {segment.key!r} is used by flag {flag.key!r}",
                            code="segment_in_use",
                        )
    before = snapshot(segment)
    session.delete(segment)
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="segment.deleted",
        entity_type="segment",
        entity_id=segment.key,
        now=now,
        before=before,
    )
    _touch_environments(ctx, project, segment.key)
