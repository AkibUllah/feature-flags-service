from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from feature_flags.clock import ensure_utc
from feature_flags.config import Settings
from feature_flags.models import Project, SdkKey
from feature_flags.services import projects
from feature_flags.services.context import AppContext


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def now(ctx: AppContext = Depends(get_ctx)) -> datetime:
    return ensure_utc(ctx.clock.now())


def actor(x_actor: str | None = Header(default=None)) -> str:
    return (x_actor or "admin-api").strip()[:100]


def require_admin(
    settings: Settings = Depends(get_settings), x_api_key: str | None = Header(default=None)
) -> None:
    if settings.admin_api_key and x_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="invalid or missing admin API key")


def project_dep(project_key: str, db: Session = Depends(get_db)) -> Project:
    return projects.get_project(db, project_key)


def sdk_key(
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
    authorization: str | None = Header(default=None),
    x_sdk_key: str | None = Header(default=None),
) -> SdkKey:
    token = x_sdk_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return projects.authenticate_sdk_key(db, token, at)
