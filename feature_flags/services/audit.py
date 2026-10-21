from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.models import AuditEntry


def record(
    session: Session,
    *,
    project_id: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    now: datetime,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    environment_key: str | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        project_id=project_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        environment_key=environment_key,
        before=before,
        after=after,
        created_at=now,
    )
    session.add(entry)
    session.flush()
    return entry


def list_entries(
    session: Session,
    project_id: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 100,
) -> list[AuditEntry]:
    stmt = select(AuditEntry).where(AuditEntry.project_id == project_id)
    if entity_type:
        stmt = stmt.where(AuditEntry.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEntry.entity_id == entity_id)
    return list(
        session.scalars(
            stmt.order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc()).limit(limit)
        )
    )
