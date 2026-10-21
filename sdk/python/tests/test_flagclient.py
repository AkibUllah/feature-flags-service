from __future__ import annotations

import json

import httpx
import pytest

from flagclient import FlagClient, FlagClientError

PAYLOAD = {
    "environment": "prod",
    "version": 3,
    "flags": {
        "new-checkout": {
            "key": "new-checkout",
            "kind": "boolean",
            "version": 2,
            "enabled": True,
            "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
            "off_variation": "off",
            "targets": [],
            "rules": [
                {
                    "id": "bd",
                    "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
                    "variation": "on",
                }
            ],
            "fallthrough": {"variation": "off"},
            "salt": "s",
        },
        "banner": {
            "key": "banner",
            "kind": "string",
            "version": 1,
            "enabled": True,
            "variations": [{"key": "eid", "value": "Eid Mubarak"}, {"key": "none", "value": ""}],
            "off_variation": "none",
            "targets": [],
            "rules": [],
            "fallthrough": {"rollout": [{"variation": "eid", "weight": 100000}]},
            "salt": "s",
        },
    },
    "segments": {},
}


class FakeServer:
    def __init__(self) -> None:
        self.payload = dict(PAYLOAD)
        self.etag = '"v3"'
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None
        self.raise_error = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_error:
            raise httpx.ConnectError("down")
        if self.fail_with:
            return httpx.Response(self.fail_with)
        if request.headers.get("If-None-Match") == self.etag:
            return httpx.Response(304, headers={"ETag": self.etag})
        return httpx.Response(200, json=self.payload, headers={"ETag": self.etag})


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def client(server) -> FlagClient:
    return FlagClient(
        "srv-test",
        "http://flags.local/",
        http=httpx.Client(transport=httpx.MockTransport(server.handler)),
        poll_interval=60,
    )


def test_requires_server_key():
    with pytest.raises(FlagClientError):
        FlagClient("cli-abc", "http://x")


def test_refresh_sends_auth_and_installs_payload(client, server):
    assert not client.ready and client.variation("new-checkout", "u1", default="dflt") == "dflt"
    assert client.refresh() is True
    assert client.ready and client.version == 3
    assert server.requests[0].headers["Authorization"] == "Bearer srv-test"
    assert server.requests[0].url.path == "/v1/sdk/flags"


def test_local_evaluation_with_reasons(client):
    client.refresh()
    assert client.variation("new-checkout", {"key": "u1", "country": "BD"}, default=False) is True
    assert client.variation("new-checkout", {"key": "u1", "country": "IN"}, default=True) is False
    assert client.is_enabled("new-checkout", "u1") is False
    result = client.evaluate("new-checkout", {"key": "u", "country": "BD"})
    assert result.reason.kind == "RULE_MATCH" and result.reason.rule_id == "bd"
    assert client.variation("banner", "u1", default="") == "Eid Mubarak"
    assert client.variation("missing", "u1", default=42) == 42
    assert client.evaluate("missing", "u1") is None
    assert client.all_flags({"key": "u1", "country": "BD"}) == {
        "new-checkout": True,
        "banner": "Eid Mubarak",
    }


def test_etag_roundtrip_keeps_payload_on_304(client, server):
    client.refresh()
    assert client.refresh() is False
    assert server.requests[1].headers["If-None-Match"] == '"v3"'
    assert client.refresh_count == 2 and client.version == 3
    server.payload = {**PAYLOAD, "version": 4}
    server.etag = '"v4"'
    assert client.refresh() is True and client.version == 4


def test_errors_keep_last_good_payload(client, server):
    client.refresh()
    server.fail_with = 503
    assert client.refresh() is False
    assert client.last_error == "HTTP 503" and client.version == 3
    server.fail_with = None
    server.raise_error = True
    assert client.refresh() is False
    assert "ConnectError" in client.last_error
    assert client.variation("banner", "u1", default="") == "Eid Mubarak"


def test_on_change_callback_and_context_manager(server):
    seen = []
    with FlagClient(
        "srv-test",
        "http://flags.local",
        http=httpx.Client(transport=httpx.MockTransport(server.handler)),
        on_change=seen.append,
        poll_interval=60,
    ) as client:
        assert client.ready and client.wait_until_ready(0.1)
        assert seen[0]["version"] == 3
    assert json.dumps(seen[0]["flags"]["banner"]["kind"]) == '"string"'
