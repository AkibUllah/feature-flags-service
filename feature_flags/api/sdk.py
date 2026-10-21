"""SDK-facing API: evaluation, bulk payload with ETag, SSE stream."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from feature_flags.api.deps import get_ctx, get_db, now, sdk_key
from feature_flags.core.clauses import Context
from feature_flags.models import Environment, SdkKey
from feature_flags.schemas import ContextIn, EvaluateRequest, EvaluationOut
from feature_flags.services import evaluation, payloads
from feature_flags.services.bus import ChangeEvent
from feature_flags.services.context import AppContext
from feature_flags.services.errors import DomainError

router = APIRouter(prefix="/v1", tags=["sdk"])


def decode_context(raw: str | None) -> Context | None:
    """`context` query parameter: base64url-encoded JSON `{"key": ..., "attributes": {...}}`."""
    if not raw:
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        parsed = ContextIn.model_validate(data)
    except Exception as exc:
        raise DomainError(f"invalid context parameter: {exc}", code="invalid_context") from exc
    return Context(parsed.key, parsed.attributes)


def _payload_for(
    session: Session, ctx: AppContext, key: SdkKey, context: Context | None
) -> tuple[dict, str]:
    env = key.environment
    payload, etag = payloads.cached_payload(
        session, ctx.kv, env, ctx.settings.payload_cache_ttl_seconds
    )
    if key.kind == "client":
        if context is None:
            raise DomainError("client SDK keys must send a context", code="context_required")
        return payloads.client_payload(payload, context), payloads.client_etag(etag, context)
    return payload, etag


@router.post("/evaluate", response_model=EvaluationOut | list[EvaluationOut])
def evaluate(
    body: EvaluateRequest,
    key: SdkKey = Depends(sdk_key),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    """Single flag (`flag`) returns one object; `flags` or neither returns a list."""
    context = Context(body.context.key, body.context.attributes)
    keys = [body.flag] if body.flag else body.flags
    results = evaluation.evaluate_flags(db, ctx, key.environment, context, keys, at)
    out = [r.as_dict() for r in results]
    return out[0] if body.flag else out


@router.get("/sdk/flags")
def sdk_flags(
    response: Response,
    key: SdkKey = Depends(sdk_key),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
    context: str | None = Query(default=None, description="base64url JSON context (client keys)"),
    if_none_match: str | None = Header(default=None),
):
    """Bulk payload. Server keys get full targeting configs for local evaluation; client keys
    get values evaluated for `context`. Supports `If-None-Match` / `304 Not Modified`."""
    payload, etag = _payload_for(db, ctx, key, decode_context(context))
    headers = {
        "ETag": etag,
        "Cache-Control": "no-cache",
        "X-Flags-Version": str(payload["version"]),
    }
    if if_none_match and etag in [tag.strip() for tag in if_none_match.split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(
        content=json.dumps(payload, separators=(",", ":")),
        media_type="application/json",
        headers=headers,
    )


def format_sse(event: str, data: dict | str, event_id: str | None = None) -> str:
    body = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {body}\n\n"


async def sse_events(
    ctx: AppContext,
    env: Environment,
    load_payload,
    *,
    heartbeat_seconds: float,
    max_events: int | None = None,
    disconnected=None,
) -> AsyncIterator[str]:
    """Yield `put` with the full payload first, then another `put` on every change.

    `load_payload()` runs in a worker thread (it hits the database). Heartbeats are SSE
    comments so proxies keep the connection open. `max_events` exists for tests and curl.
    """
    sent = 0
    payload, etag = await asyncio.to_thread(load_payload)
    yield format_sse("put", payload, event_id=etag.strip('"'))
    sent += 1
    last_version = payload["version"]
    if max_events is not None and sent >= max_events:
        return
    with ctx.bus.subscribe(env.id) as queue:
        while True:
            if disconnected is not None and await disconnected():
                return
            try:
                event: ChangeEvent = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            # Coalesce bursts: drain whatever else is queued before rebuilding the payload.
            while not queue.empty():
                event = queue.get_nowait()
            if event.version <= last_version:
                continue
            payload, etag = await asyncio.to_thread(load_payload)
            last_version = payload["version"]
            yield format_sse("put", payload, event_id=etag.strip('"'))
            sent += 1
            if max_events is not None and sent >= max_events:
                return


@router.get("/sdk/stream")
async def sdk_stream(
    request: Request,
    key: SdkKey = Depends(sdk_key),
    ctx: AppContext = Depends(get_ctx),
    context: str | None = Query(default=None),
    max_events: int | None = Query(default=None, ge=1, le=1000),
):
    """Server-Sent Events: `put` events carrying the same payload as `GET /v1/sdk/flags`."""
    parsed = decode_context(context)
    if key.kind == "client" and parsed is None:
        raise DomainError("client SDK keys must send a context", code="context_required")
    session_factory = request.app.state.session_factory
    env = key.environment

    def load_payload():
        session = session_factory()
        try:
            fresh_env = session.get(Environment, env.id)
            return _payload_for(session, ctx, _KeyView(key.kind, fresh_env), parsed)
        finally:
            session.close()

    generator = sse_events(
        ctx,
        env,
        load_payload,
        heartbeat_seconds=ctx.settings.sse_heartbeat_seconds,
        max_events=max_events,
        disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class _KeyView:
    """Just enough of an SdkKey for `_payload_for` when reloading inside the stream thread."""

    def __init__(self, kind: str, environment: Environment) -> None:
        self.kind = kind
        self.environment = environment
