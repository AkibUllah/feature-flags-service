"""Flag change webhooks: signed POSTs to every active endpoint of the project."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from feature_flags.core.signing import HEADER, sign
from feature_flags.models import Project, WebhookEndpoint
from feature_flags.services.context import AppContext
from feature_flags.services.errors import NotFound

log = logging.getLogger(__name__)


def create_endpoint(
    session: Session,
    project: Project,
    *,
    url: str,
    now: datetime,
    secret: str | None = None,
    description: str | None = None,
) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        project_id=project.id,
        url=url,
        secret=secret or secrets.token_urlsafe(32),
        description=description,
        created_at=now,
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def list_endpoints(session: Session, project: Project) -> list[WebhookEndpoint]:
    return list(
        session.scalars(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.project_id == project.id)
            .order_by(WebhookEndpoint.created_at)
        )
    )


def delete_endpoint(session: Session, project: Project, endpoint_id: str) -> None:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.project_id != project.id:
        raise NotFound(f"webhook {endpoint_id!r} not found")
    session.delete(endpoint)
    session.flush()


def notify(
    session: Session,
    ctx: AppContext,
    project: Project,
    *,
    event_type: str,
    payload: dict[str, Any],
    now: datetime,
) -> int:
    endpoints = [e for e in list_endpoints(session, project) if e.active]
    if not endpoints:
        return 0
    body = json.dumps(
        {
            "type": event_type,
            "project": project.key,
            "created_at": now.isoformat(),
            "data": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    timestamp = int(now.timestamp())
    delivered = 0
    for endpoint in endpoints:
        headers = {
            "Content-Type": "application/json",
            HEADER: sign(endpoint.secret, timestamp, body),
            "X-Flags-Event": event_type,
        }
        try:
            response = ctx.http.post(endpoint.url, content=body, headers=headers)
            endpoint.last_status_code = response.status_code
            endpoint.last_error = None if response.is_success else f"HTTP {response.status_code}"
            delivered += int(response.is_success)
        except httpx.HTTPError as exc:
            endpoint.last_status_code = None
            endpoint.last_error = f"{type(exc).__name__}: {exc}"[:500]
            log.warning("webhook %s failed: %s", endpoint.url, endpoint.last_error)
        endpoint.last_delivery_at = now
    return delivered
