"""Management API: projects, environments, SDK keys, flags, segments, schedules, audit, webhooks."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from feature_flags.api.deps import actor, get_ctx, get_db, now, project_dep
from feature_flags.models import Project
from feature_flags.schemas import (
    AuditOut,
    EnvironmentCreate,
    EnvironmentOut,
    FlagConfigOut,
    FlagConfigPatch,
    FlagCreate,
    FlagDetail,
    FlagOut,
    FlagStatsOut,
    FlagUpdate,
    ProjectCreate,
    ProjectOut,
    ScheduledChangeCreate,
    ScheduledChangeOut,
    SdkKeyCreate,
    SdkKeyCreated,
    SdkKeyOut,
    SegmentCreate,
    SegmentOut,
    SegmentUpdate,
    WebhookCreate,
    WebhookCreated,
    WebhookOut,
)
from feature_flags.services import audit, flags, projects, scheduled, segments, stats, webhooks
from feature_flags.services.context import AppContext
from feature_flags.services.projects import DEFAULT_ENVIRONMENTS

router = APIRouter(prefix="/v1", tags=["admin"])


def _detail(project: Project, flag) -> dict:
    env_by_id = {env.id: env.key for env in project.environments}
    return FlagDetail(
        **FlagOut.model_validate(flag).model_dump(),
        configs={
            env_by_id[c.environment_id]: FlagConfigOut.model_validate(c)
            for c in flag.configs
            if c.environment_id in env_by_id
        },
    ).model_dump()


# projects ---------------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    envs = (
        tuple((e, e.title()) for e in body.environments)
        if body.environments
        else DEFAULT_ENVIRONMENTS
    )
    return projects.create_project(
        db, key=body.key, name=body.name, now=at, actor=who, environments=envs
    )


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return projects.list_projects(db)


@router.get("/projects/{project_key}", response_model=ProjectOut)
def get_project(project: Project = Depends(project_dep)):
    return project


@router.post("/projects/{project_key}/environments", response_model=EnvironmentOut, status_code=201)
def create_environment(
    body: EnvironmentCreate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return projects.create_environment(db, project, key=body.key, name=body.name, now=at, actor=who)


@router.post(
    "/projects/{project_key}/environments/{env_key}/sdk-keys",
    response_model=SdkKeyCreated,
    status_code=201,
)
def create_sdk_key(
    env_key: str,
    body: SdkKeyCreate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    env = projects.get_environment(db, project, env_key)
    row, plaintext = projects.create_sdk_key(
        db, env, kind=body.kind, name=body.name, now=at, actor=who
    )
    return SdkKeyCreated(**SdkKeyOut.model_validate(row).model_dump(), key=plaintext)


@router.get(
    "/projects/{project_key}/environments/{env_key}/sdk-keys", response_model=list[SdkKeyOut]
)
def list_sdk_keys(
    env_key: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)
):
    return projects.list_sdk_keys(db, projects.get_environment(db, project, env_key))


@router.delete(
    "/projects/{project_key}/environments/{env_key}/sdk-keys/{key_id}", response_model=SdkKeyOut
)
def revoke_sdk_key(
    env_key: str,
    key_id: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return projects.revoke_sdk_key(
        db, projects.get_environment(db, project, env_key), key_id, now=at, actor=who
    )


# flags ------------------------------------------------------------------------------------


@router.post("/projects/{project_key}/flags", response_model=FlagDetail, status_code=201)
def create_flag(
    body: FlagCreate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    flag = flags.create_flag(
        db,
        ctx,
        project,
        key=body.key,
        name=body.name,
        kind=body.kind,
        variations=[v.model_dump(exclude_none=True) for v in body.variations],
        description=body.description,
        tags=body.tags,
        off_variation=body.off_variation,
        default_variation=body.default_variation,
        now=at,
        actor=who,
    )
    return _detail(project, flag)


@router.get("/projects/{project_key}/flags", response_model=list[FlagOut])
def list_flags(
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    include_archived: bool = False,
    tag: str | None = None,
):
    return flags.list_flags(db, project, include_archived=include_archived, tag=tag)


@router.get("/projects/{project_key}/flags/{flag_key}", response_model=FlagDetail)
def get_flag(flag_key: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)):
    return _detail(project, flags.get_flag(db, project, flag_key))


@router.patch("/projects/{project_key}/flags/{flag_key}", response_model=FlagDetail)
def update_flag(
    flag_key: str,
    body: FlagUpdate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    flag = flags.update_metadata(
        db, project, flags.get_flag(db, project, flag_key), now=at, actor=who, **body.model_dump()
    )
    return _detail(project, flag)


@router.delete("/projects/{project_key}/flags/{flag_key}", response_model=FlagOut)
def archive_flag(
    flag_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return flags.archive_flag(
        db, ctx, project, flags.get_flag(db, project, flag_key), now=at, actor=who
    )


@router.get(
    "/projects/{project_key}/flags/{flag_key}/environments/{env_key}", response_model=FlagConfigOut
)
def get_config(
    flag_key: str,
    env_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
):
    return flags.get_config(
        flags.get_flag(db, project, flag_key), projects.get_environment(db, project, env_key)
    )


@router.patch(
    "/projects/{project_key}/flags/{flag_key}/environments/{env_key}", response_model=FlagConfigOut
)
def patch_config(
    flag_key: str,
    env_key: str,
    body: FlagConfigPatch,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    flag = flags.get_flag(db, project, flag_key, include_archived=False)
    env = projects.get_environment(db, project, env_key)
    return flags.update_config(
        db, ctx, project, flag, env, body.model_dump(exclude_none=True), now=at, actor=who
    )


@router.post(
    "/projects/{project_key}/flags/{flag_key}/environments/{env_key}/kill",
    response_model=FlagConfigOut,
)
def kill(
    flag_key: str,
    env_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    """Kill switch: serve the off variation to everyone, keeping the rules for later."""
    return flags.set_enabled(
        db,
        ctx,
        project,
        flags.get_flag(db, project, flag_key),
        projects.get_environment(db, project, env_key),
        False,
        now=at,
        actor=who,
    )


@router.post(
    "/projects/{project_key}/flags/{flag_key}/environments/{env_key}/restore",
    response_model=FlagConfigOut,
)
def restore(
    flag_key: str,
    env_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return flags.set_enabled(
        db,
        ctx,
        project,
        flags.get_flag(db, project, flag_key),
        projects.get_environment(db, project, env_key),
        True,
        now=at,
        actor=who,
    )


@router.post(
    "/projects/{project_key}/flags/{flag_key}/environments/{env_key}/schedule",
    response_model=ScheduledChangeOut,
    status_code=201,
)
def schedule_change(
    flag_key: str,
    env_key: str,
    body: ScheduledChangeCreate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    from feature_flags.clock import ensure_utc

    return scheduled.schedule(
        db,
        project,
        flags.get_flag(db, project, flag_key),
        projects.get_environment(db, project, env_key),
        scheduled_at=ensure_utc(body.scheduled_at),
        changes=body.changes.model_dump(exclude_none=True),
        comment=body.comment,
        now=at,
        actor=who,
    )


@router.get(
    "/projects/{project_key}/flags/{flag_key}/schedule", response_model=list[ScheduledChangeOut]
)
def list_schedule(
    flag_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    status: str | None = None,
):
    return scheduled.list_for_flag(db, flags.get_flag(db, project, flag_key), status=status)


@router.delete(
    "/projects/{project_key}/flags/{flag_key}/schedule/{change_id}",
    response_model=ScheduledChangeOut,
)
def cancel_schedule(
    flag_key: str,
    change_id: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return scheduled.cancel(
        db, project, flags.get_flag(db, project, flag_key), change_id, now=at, actor=who
    )


@router.post("/scheduled-changes/apply", response_model=list[ScheduledChangeOut])
def apply_scheduled(
    db: Session = Depends(get_db), ctx: AppContext = Depends(get_ctx), at: datetime = Depends(now)
):
    """Apply due changes now (the background scheduler does this on an interval too)."""
    return scheduled.apply_due(db, ctx, at)


@router.get("/projects/{project_key}/flags/{flag_key}/stats", response_model=FlagStatsOut)
def flag_stats(
    flag_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
):
    flag = flags.get_flag(db, project, flag_key)
    return FlagStatsOut(
        flag=flag.key,
        environments={
            env.key: stats.read(ctx.kv, env.id, flag.key) for env in project.environments
        },
    )


# segments ---------------------------------------------------------------------------------


@router.post("/projects/{project_key}/segments", response_model=SegmentOut, status_code=201)
def create_segment(
    body: SegmentCreate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return segments.create_segment(db, ctx, project, now=at, actor=who, **body.model_dump())


@router.get("/projects/{project_key}/segments", response_model=list[SegmentOut])
def list_segments(project: Project = Depends(project_dep), db: Session = Depends(get_db)):
    return segments.list_segments(db, project)


@router.get("/projects/{project_key}/segments/{segment_key}", response_model=SegmentOut)
def get_segment(
    segment_key: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)
):
    return segments.get_segment(db, project, segment_key)


@router.patch("/projects/{project_key}/segments/{segment_key}", response_model=SegmentOut)
def update_segment(
    segment_key: str,
    body: SegmentUpdate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    return segments.update_segment(
        db,
        ctx,
        project,
        segments.get_segment(db, project, segment_key),
        body.model_dump(exclude_none=True),
        now=at,
        actor=who,
    )


@router.delete("/projects/{project_key}/segments/{segment_key}", status_code=204)
def delete_segment(
    segment_key: str,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
    who: str = Depends(actor),
):
    segments.delete_segment(
        db, ctx, project, segments.get_segment(db, project, segment_key), now=at, actor=who
    )


# audit / webhooks -------------------------------------------------------------------------


@router.get("/projects/{project_key}/audit", response_model=list[AuditOut])
def list_audit(
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(100, le=500),
):
    return audit.list_entries(
        db, project.id, entity_type=entity_type, entity_id=entity_id, limit=limit
    )


@router.post("/projects/{project_key}/webhooks", response_model=WebhookCreated, status_code=201)
def create_webhook(
    body: WebhookCreate,
    project: Project = Depends(project_dep),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    return webhooks.create_endpoint(
        db, project, url=body.url, secret=body.secret, description=body.description, now=at
    )


@router.get("/projects/{project_key}/webhooks", response_model=list[WebhookOut])
def list_webhooks(project: Project = Depends(project_dep), db: Session = Depends(get_db)):
    return webhooks.list_endpoints(db, project)


@router.delete("/projects/{project_key}/webhooks/{endpoint_id}", status_code=204)
def delete_webhook(
    endpoint_id: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)
):
    webhooks.delete_endpoint(db, project, endpoint_id)
