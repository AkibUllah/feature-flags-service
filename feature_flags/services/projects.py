from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.models import Environment, Project, SdkKey
from feature_flags.services import audit
from feature_flags.services.errors import Conflict, NotFound, Unauthorized

DEFAULT_ENVIRONMENTS = (("dev", "Development"), ("staging", "Staging"), ("prod", "Production"))
KEY_PREFIXES = {"server": "srv", "client": "cli"}


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_project(
    session: Session,
    *,
    key: str,
    name: str,
    now: datetime,
    actor: str,
    environments: tuple[tuple[str, str], ...] = DEFAULT_ENVIRONMENTS,
) -> Project:
    if session.scalar(select(Project).where(Project.key == key)):
        raise Conflict(f"project {key!r} already exists", code="duplicate_key")
    project = Project(key=key, name=name, created_at=now)
    for env_key, env_name in environments:
        project.environments.append(Environment(key=env_key, name=env_name, created_at=now))
    session.add(project)
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="project.created",
        entity_type="project",
        entity_id=project.key,
        now=now,
        after={"key": key, "name": name, "environments": [e for e, _ in environments]},
    )
    return project


def get_project(session: Session, key: str) -> Project:
    project = session.scalar(select(Project).where(Project.key == key))
    if project is None:
        project = session.get(Project, key)
    if project is None:
        raise NotFound(f"project {key!r} not found")
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.created_at, Project.key)))


def create_environment(
    session: Session, project: Project, *, key: str, name: str, now: datetime, actor: str
) -> Environment:
    if any(env.key == key for env in project.environments):
        raise Conflict(f"environment {key!r} already exists", code="duplicate_key")
    env = Environment(project_id=project.id, key=key, name=name, created_at=now)
    project.environments.append(env)
    session.flush()
    audit.record(
        session,
        project_id=project.id,
        actor=actor,
        action="environment.created",
        entity_type="environment",
        entity_id=key,
        now=now,
        after={"key": key, "name": name},
    )
    return env


def get_environment(session: Session, project: Project, key: str) -> Environment:
    for env in project.environments:
        if env.key == key or env.id == key:
            return env
    raise NotFound(f"environment {key!r} not found in project {project.key!r}")


def bump_environment(env: Environment) -> int:
    env.version += 1
    return env.version


def create_sdk_key(
    session: Session, env: Environment, *, kind: str, name: str, now: datetime, actor: str
) -> tuple[SdkKey, str]:
    """Returns the row and the plaintext key, which is only ever shown once."""
    if kind not in KEY_PREFIXES:
        raise ValueError("kind must be 'server' or 'client'")
    plaintext = f"{KEY_PREFIXES[kind]}-{secrets.token_hex(20)}"
    row = SdkKey(
        environment_id=env.id,
        kind=kind,
        name=name,
        prefix=plaintext[:12],
        hashed_secret=hash_secret(plaintext),
        created_at=now,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        project_id=env.project_id,
        actor=actor,
        action="sdk_key.created",
        entity_type="sdk_key",
        entity_id=row.id,
        environment_key=env.key,
        now=now,
        after={"kind": kind, "name": name, "prefix": row.prefix},
    )
    return row, plaintext


def list_sdk_keys(session: Session, env: Environment) -> list[SdkKey]:
    return list(
        session.scalars(
            select(SdkKey).where(SdkKey.environment_id == env.id).order_by(SdkKey.created_at)
        )
    )


def revoke_sdk_key(
    session: Session, env: Environment, key_id: str, *, now: datetime, actor: str
) -> SdkKey:
    row = session.get(SdkKey, key_id)
    if row is None or row.environment_id != env.id:
        raise NotFound(f"sdk key {key_id!r} not found")
    if row.revoked_at is None:
        row.revoked_at = now
        audit.record(
            session,
            project_id=env.project_id,
            actor=actor,
            action="sdk_key.revoked",
            entity_type="sdk_key",
            entity_id=row.id,
            environment_key=env.key,
            now=now,
            before={"prefix": row.prefix, "kind": row.kind},
        )
    return row


def authenticate_sdk_key(session: Session, plaintext: str | None, now: datetime) -> SdkKey:
    if not plaintext:
        raise Unauthorized("missing SDK key")
    row = session.scalar(select(SdkKey).where(SdkKey.hashed_secret == hash_secret(plaintext)))
    if row is None or row.revoked_at is not None:
        raise Unauthorized("invalid or revoked SDK key")
    row.last_used_at = now
    return row
