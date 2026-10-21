from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class UTCDateTime(TypeDecorator):
    """Store aware UTC datetimes as naive UTC and hand them back aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime; use aware UTC values")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("prj"))
    key: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]

    environments: Mapped[list[Environment]] = relationship(
        back_populates="project", order_by="Environment.created_at", lazy="selectin"
    )


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_env_project_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("env"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    # Bumped on every change that affects this environment's SDK payload; drives ETags and SSE.
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime]

    project: Mapped[Project] = relationship(back_populates="environments")


class SdkKey(Base):
    __tablename__ = "sdk_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("key"))
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # server | client
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(16))
    hashed_secret: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]

    environment: Mapped[Environment] = relationship(lazy="joined")


class Flag(Base):
    __tablename__ = "flags"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_flag_project_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("flg"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10))  # boolean | string | number | json
    variations: Mapped[list[Any]] = mapped_column(JSON)  # [{"key": "on", "value": true}, ...]
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    salt: Mapped[str] = mapped_column(String(32), default=lambda: secrets.token_hex(8))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    configs: Mapped[list[FlagConfig]] = relationship(
        back_populates="flag", cascade="all, delete-orphan", lazy="selectin"
    )


class FlagConfig(Base):
    """Per-environment targeting for a flag: this is what 'per-environment overrides' are."""

    __tablename__ = "flag_configs"
    __table_args__ = (UniqueConstraint("flag_id", "environment_id", name="uq_config_flag_env"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flag_id: Mapped[str] = mapped_column(ForeignKey("flags.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    off_variation: Mapped[str] = mapped_column(String(100))
    targets: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rules: Mapped[list[Any]] = mapped_column(JSON, default=list)
    fallthrough: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime]

    flag: Mapped[Flag] = relationship(back_populates="configs")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_segment_project_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("seg"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    included: Mapped[list[Any]] = mapped_column(JSON, default=list)
    excluded: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rules: Mapped[list[Any]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ScheduledChange(Base):
    __tablename__ = "scheduled_changes"
    __table_args__ = (Index("ix_scheduled_status_at", "status", "scheduled_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("sch"))
    flag_id: Mapped[str] = mapped_column(ForeignKey("flags.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"), index=True)
    scheduled_at: Mapped[datetime]
    changes: Mapped[dict[str, Any]] = mapped_column(JSON)  # partial config patch
    comment: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|applied|canceled
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime]
    applied_at: Mapped[datetime | None]


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(200))
    environment_key: Mapped[str | None] = mapped_column(String(60))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(index=True)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("whk"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    secret: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_delivery_at: Mapped[datetime | None]
