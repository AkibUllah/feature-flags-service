"""Scheduled flag changes: a config patch that is applied when its time comes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.models import Environment, Flag, Project, ScheduledChange
from feature_flags.services import audit, flags
from feature_flags.services.context import AppContext
from feature_flags.services.errors import DomainError, NotFound
from feature_flags.services.validation import validate_config


def schedule(
    session: Session,
    project: Project,
    flag: Flag,
    env: Environment,
    *,
    scheduled_at: datetime,
    changes: dict[str, Any],
    now: datetime,
    actor: str,
    comment: str | None = None,
) -> ScheduledChange:
    if scheduled_at <= now:
        raise DomainError("scheduled_at must be in the future", code="not_in_future")
    unknown = set(changes) - set(flags.CONFIG_FIELDS)
    if unknown or not changes:
        raise DomainError(f"changes must be a non-empty subset of {list(flags.CONFIG_FIELDS)}")
    # Validate against the current config so obviously broken patches fail now, not at 3am.
    current = flags.config_snapshot(flags.get_config(flag, env))
    merged = {k: current[k] for k in flags.CONFIG_FIELDS}
    merged.update(changes)
    validate_config(merged, flag.variations, flags.segment_keys(session, project.id))
    change = ScheduledChange(
        flag_id=flag.id,
        environment_id=env.id,
        scheduled_at=scheduled_at,
        changes=changes,
        comment=comment,
        created_by=actor,
        created_at=now,
    )
    session.add(change)
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="flag.change_scheduled",
        entity_type="flag",
        entity_id=flag.key,
        environment_key=env.key,
        now=now,
        after={"scheduled_at": scheduled_at.isoformat(), "changes": changes, "id": change.id},
    )
    return change


def list_for_flag(
    session: Session, flag: Flag, env: Environment | None = None, status: str | None = None
) -> list[ScheduledChange]:
    stmt = select(ScheduledChange).where(ScheduledChange.flag_id == flag.id)
    if env is not None:
        stmt = stmt.where(ScheduledChange.environment_id == env.id)
    if status:
        stmt = stmt.where(ScheduledChange.status == status)
    return list(session.scalars(stmt.order_by(ScheduledChange.scheduled_at)))


def cancel(
    session: Session, project: Project, flag: Flag, change_id: str, *, now: datetime, actor: str
) -> ScheduledChange:
    change = session.get(ScheduledChange, change_id)
    if change is None or change.flag_id != flag.id:
        raise NotFound(f"scheduled change {change_id!r} not found")
    if change.status != "pending":
        raise DomainError(f"scheduled change is {change.status}", code="not_pending")
    change.status = "canceled"
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="flag.schedule_canceled",
        entity_type="flag",
        entity_id=flag.key,
        now=now,
        before={"id": change.id, "changes": change.changes},
    )
    return change


def apply_due(session: Session, ctx: AppContext, now: datetime) -> list[ScheduledChange]:
    """Apply every pending change whose time has come. Safe to call as often as you like."""
    due = session.scalars(
        select(ScheduledChange)
        .where(ScheduledChange.status == "pending", ScheduledChange.scheduled_at <= now)
        .order_by(ScheduledChange.scheduled_at, ScheduledChange.id)
    ).all()
    applied: list[ScheduledChange] = []
    for change in due:
        flag = session.get(Flag, change.flag_id)
        env = session.get(Environment, change.environment_id)
        project = session.get(Project, flag.project_id)
        try:
            flags.update_config(
                session,
                ctx,
                project,
                flag,
                env,
                change.changes,
                now=now,
                actor=f"scheduler:{change.created_by}",
                action="flag.scheduled_change_applied",
            )
            change.status = "applied"
            change.applied_at = now
            applied.append(change)
        except DomainError as exc:
            change.status = "failed"
            change.comment = f"{change.comment or ''} [failed: {exc.message}]".strip()
            audit.record(
                session,
                project_id=project.id,
                actor="scheduler",
                action="flag.scheduled_change_failed",
                entity_type="flag",
                entity_id=flag.key,
                environment_key=env.key,
                now=now,
                before={"id": change.id, "changes": change.changes, "error": exc.message},
            )
    session.flush()
    return applied
