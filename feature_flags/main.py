from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from feature_flags import __version__
from feature_flags.api import admin, sdk
from feature_flags.api.deps import get_ctx, get_db, require_admin
from feature_flags.clock import Clock
from feature_flags.config import Settings, get_settings
from feature_flags.database import init_db, make_engine, make_session_factory
from feature_flags.kv import KV
from feature_flags.logging_config import configure_logging
from feature_flags.schemas import ErrorResponse, HealthOut
from feature_flags.services import scheduled
from feature_flags.services.context import AppContext
from feature_flags.services.errors import DomainError

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    kv: KV | None = None,
    transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    ctx = AppContext.from_settings(settings, kv=kv, clock=clock, transport=transport)

    async def scheduler_loop() -> None:
        while True:
            await asyncio.sleep(settings.scheduler_interval_seconds)
            try:
                session = session_factory()
                try:
                    applied = scheduled.apply_due(session, ctx, ctx.clock.now())
                    session.commit()
                    if applied:
                        log.info("applied %d scheduled change(s)", len(applied))
                finally:
                    session.close()
            except Exception:  # pragma: no cover - keep the loop alive
                log.exception("scheduled-change sweep failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        task = (
            asyncio.create_task(scheduler_loop())
            if settings.scheduler_interval_seconds > 0
            else None
        )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            engine.dispose()

    app = FastAPI(
        title="Feature Flags Service",
        version=__version__,
        description="Targeting rules, sticky rollouts, segments, scheduled changes, ETag + SSE.",
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.ctx = ctx

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(admin.router, dependencies=[Depends(require_admin)])
    app.include_router(sdk.router)

    @app.get("/health", response_model=HealthOut, tags=["ops"])
    def health(db=Depends(get_db), ctx: AppContext = Depends(get_ctx)):
        db.execute(text("SELECT 1"))
        return HealthOut(
            status="ok",
            database="ok",
            cache=f"{ctx.kv.name}:{'ok' if ctx.kv.ping() else 'down'}",
            sse_subscribers=ctx.bus.subscriber_count(),
            clock=ctx.clock.now(),
            version=__version__,
        )

    return app


app = create_app()
