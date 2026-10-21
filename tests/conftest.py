from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from feature_flags.clock import FixedClock
from feature_flags.config import Settings
from feature_flags.kv import RedisKV
from feature_flags.main import create_app
from feature_flags.services import flags, projects

ADMIN = {"X-API-Key": "test-admin-key", "X-Actor": "akib"}
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

BOOL_VARIATIONS = [{"key": "on", "value": True}, {"key": "off", "value": False}]


@dataclass
class FakeReceiver:
    requests: list[httpx.Request] = field(default_factory=list)
    status: int = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": True})


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        admin_api_key="test-admin-key",
        scheduler_interval_seconds=0,
        sse_heartbeat_seconds=0.05,
        log_level="WARNING",
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def kv() -> RedisKV:
    return RedisKV(fakeredis.FakeRedis())


@pytest.fixture
def receiver() -> FakeReceiver:
    return FakeReceiver()


@pytest.fixture
def app(settings, clock, kv, receiver):
    return create_app(settings, clock=clock, kv=kv, transport=httpx.MockTransport(receiver.handler))


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ctx(app):
    return app.state.ctx


@pytest.fixture
def session(client, app):
    s = app.state.session_factory()
    try:
        yield s
        s.commit()
    finally:
        s.close()


@pytest.fixture
def project(session):
    return projects.create_project(session, key="shop", name="Shop", now=T0, actor="akib")


@pytest.fixture
def prod(session, project):
    return projects.get_environment(session, project, "prod")


@pytest.fixture
def bool_flag(session, ctx, project):
    return flags.create_flag(
        session,
        ctx,
        project,
        key="new-checkout",
        name="New checkout",
        kind="boolean",
        variations=BOOL_VARIATIONS,
        now=T0,
        actor="akib",
    )


def enable(session, ctx, project, flag, env, **changes):
    changes.setdefault("enabled", True)
    return flags.update_config(session, ctx, project, flag, env, changes, now=T0, actor="akib")


def server_key(session, env) -> str:
    _, plaintext = projects.create_sdk_key(
        session, env, kind="server", name="backend", now=T0, actor="akib"
    )
    session.commit()
    return plaintext


def client_key(session, env) -> str:
    _, plaintext = projects.create_sdk_key(
        session, env, kind="client", name="web", now=T0, actor="akib"
    )
    session.commit()
    return plaintext
