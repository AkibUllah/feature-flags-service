from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- projects / environments / keys ---------------------------------------------------------


class EnvironmentOut(ORMModel):
    id: str
    key: str
    name: str
    version: int
    created_at: datetime


class ProjectCreate(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    environments: list[str] | None = Field(
        default=None, description="Defaults to dev, staging, prod"
    )


class ProjectOut(ORMModel):
    id: str
    key: str
    name: str
    created_at: datetime
    environments: list[EnvironmentOut]


class EnvironmentCreate(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)


class SdkKeyCreate(BaseModel):
    kind: str = Field(pattern="^(server|client)$")
    name: str = Field(min_length=1, max_length=200)


class SdkKeyOut(ORMModel):
    id: str
    kind: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class SdkKeyCreated(SdkKeyOut):
    key: str


# --- flags ----------------------------------------------------------------------------------


class Variation(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: Any
    name: str | None = None


class FlagCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern="^(boolean|string|number|json)$")
    variations: list[Variation] = Field(min_length=2)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    off_variation: str | None = None
    default_variation: str | None = None


class FlagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] | None = None


class FlagConfigOut(ORMModel):
    environment_id: str
    enabled: bool
    off_variation: str
    targets: list[Any]
    rules: list[Any]
    fallthrough: dict[str, Any]
    version: int
    updated_at: datetime


class FlagOut(ORMModel):
    id: str
    key: str
    name: str
    description: str | None
    kind: str
    variations: list[Any]
    tags: list[Any]
    archived: bool
    created_at: datetime
    updated_at: datetime


class FlagDetail(FlagOut):
    configs: dict[str, FlagConfigOut]


class FlagConfigPatch(BaseModel):
    enabled: bool | None = None
    off_variation: str | None = None
    targets: list[dict[str, Any]] | None = None
    rules: list[dict[str, Any]] | None = None
    fallthrough: dict[str, Any] | None = None


class ScheduledChangeCreate(BaseModel):
    scheduled_at: datetime
    changes: FlagConfigPatch
    comment: str | None = Field(default=None, max_length=300)


class ScheduledChangeOut(ORMModel):
    id: str
    flag_id: str
    environment_id: str
    scheduled_at: datetime
    changes: dict[str, Any]
    comment: str | None
    status: str
    created_by: str
    created_at: datetime
    applied_at: datetime | None


# --- segments -------------------------------------------------------------------------------


class SegmentCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    included: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class SegmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    included: list[str] | None = None
    excluded: list[str] | None = None
    rules: list[dict[str, Any]] | None = None


class SegmentOut(ORMModel):
    id: str
    key: str
    name: str
    description: str | None
    included: list[Any]
    excluded: list[Any]
    rules: list[Any]
    version: int
    created_at: datetime
    updated_at: datetime


# --- audit / webhooks / stats ---------------------------------------------------------------


class AuditOut(ORMModel):
    id: int
    actor: str
    action: str
    entity_type: str
    entity_id: str
    environment_key: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


class WebhookCreate(BaseModel):
    url: str = Field(pattern=r"^https?://", max_length=2000)
    secret: str | None = Field(default=None, min_length=16, max_length=128)
    description: str | None = Field(default=None, max_length=300)


class WebhookOut(ORMModel):
    id: str
    url: str
    description: str | None
    active: bool
    created_at: datetime
    last_status_code: int | None
    last_error: str | None
    last_delivery_at: datetime | None


class WebhookCreated(WebhookOut):
    secret: str


class FlagStatsOut(BaseModel):
    flag: str
    environments: dict[str, dict[str, Any]]


# --- SDK ------------------------------------------------------------------------------------


class ContextIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    context: ContextIn
    flag: str | None = Field(default=None, description="Evaluate one flag")
    flags: list[str] | None = Field(
        default=None, description="Evaluate these flags; omit both for all"
    )


class EvaluationOut(BaseModel):
    flag_key: str
    variation: str | None
    value: Any
    reason: dict[str, Any]
    version: int


class HealthOut(BaseModel):
    status: str
    database: str
    cache: str
    sse_subscribers: int
    clock: datetime
    version: str
