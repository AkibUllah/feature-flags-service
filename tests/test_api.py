import base64
import json
from datetime import timedelta

from tests.conftest import ADMIN, BOOL_VARIATIONS, T0


def b64(ctx: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(ctx).encode()).decode().rstrip("=")


def setup_project(client, flag_rules=None):
    assert (
        client.post("/v1/projects", json={"key": "shop", "name": "Shop"}, headers=ADMIN).status_code
        == 201
    )
    r = client.post(
        "/v1/projects/shop/flags",
        json={
            "key": "new-checkout",
            "name": "New checkout",
            "kind": "boolean",
            "variations": BOOL_VARIATIONS,
            "tags": ["checkout"],
        },
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    patch = {"enabled": True, "fallthrough": {"variation": "off"}}
    if flag_rules is not None:
        patch["rules"] = flag_rules
    r = client.patch(
        "/v1/projects/shop/flags/new-checkout/environments/prod", json=patch, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/v1/projects/shop/environments/prod/sdk-keys",
        json={"kind": "server", "name": "backend"},
        headers=ADMIN,
    )
    server = r.json()["key"]
    r = client.post(
        "/v1/projects/shop/environments/prod/sdk-keys",
        json={"kind": "client", "name": "web"},
        headers=ADMIN,
    )
    return server, r.json()["key"]


BD_RULE = [
    {
        "id": "bd",
        "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
        "variation": "on",
    }
]


def test_health_and_admin_auth(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["cache"] == "redis:ok" and h["sse_subscribers"] == 0
    assert client.get("/v1/projects").status_code == 401
    assert client.get("/v1/projects", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/v1/projects", headers=ADMIN).json() == []


def test_project_flag_crud_and_detail(client):
    setup_project(client)
    project = client.get("/v1/projects/shop", headers=ADMIN).json()
    assert [e["key"] for e in project["environments"]] == ["dev", "staging", "prod"]
    detail = client.get("/v1/projects/shop/flags/new-checkout", headers=ADMIN).json()
    assert (
        detail["configs"]["prod"]["enabled"] is True
        and detail["configs"]["dev"]["enabled"] is False
    )
    assert detail["configs"]["prod"]["version"] == 2
    r = client.patch(
        "/v1/projects/shop/flags/new-checkout",
        json={"description": "Checkout v2", "tags": ["checkout", "q1"]},
        headers=ADMIN,
    )
    assert r.json()["tags"] == ["checkout", "q1"]
    assert (
        client.get("/v1/projects/shop/flags?tag=q1", headers=ADMIN).json()[0]["key"]
        == "new-checkout"
    )
    assert client.get("/v1/projects/shop/flags?tag=none", headers=ADMIN).json() == []
    r = client.post(
        "/v1/projects/shop/flags",
        json={"key": "new-checkout", "name": "x", "kind": "boolean", "variations": BOOL_VARIATIONS},
        headers=ADMIN,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_key"
    assert client.get("/v1/projects/shop/flags/ghost", headers=ADMIN).status_code == 404
    assert (
        client.delete("/v1/projects/shop/flags/new-checkout", headers=ADMIN).json()["archived"]
        is True
    )
    assert client.get("/v1/projects/shop/flags", headers=ADMIN).json() == []
    assert (
        len(client.get("/v1/projects/shop/flags?include_archived=true", headers=ADMIN).json()) == 1
    )
    r = client.patch(
        "/v1/projects/shop/flags/new-checkout/environments/prod",
        json={"enabled": False},
        headers=ADMIN,
    )
    assert r.status_code == 404  # archived flags cannot be reconfigured


def test_evaluate_single_bulk_and_reasons(client):
    server, _ = setup_project(client, BD_RULE)
    auth = {"Authorization": f"Bearer {server}"}
    r = client.post(
        "/v1/evaluate",
        json={"context": {"key": "u1", "attributes": {"country": "BD"}}, "flag": "new-checkout"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] is True and r.json()["reason"] == {
        "kind": "RULE_MATCH",
        "rule_id": "bd",
        "rule_index": 0,
    }
    r = client.post("/v1/evaluate", json={"context": {"key": "u2"}}, headers={"X-SDK-Key": server})
    assert (
        isinstance(r.json(), list)
        and r.json()[0]["reason"] == {"kind": "FALLTHROUGH"}
        and r.json()[0]["value"] is False
    )
    r = client.post(
        "/v1/evaluate", json={"context": {"key": "u2"}, "flags": ["ghost"]}, headers=auth
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_flag"
    assert (
        client.post(
            "/v1/evaluate",
            json={"context": {"key": "u"}},
            headers={"Authorization": "Bearer srv-bad"},
        ).status_code
        == 401
    )
    assert client.post("/v1/evaluate", json={"context": {"key": "u"}}).status_code == 401
    stats = client.get("/v1/projects/shop/flags/new-checkout/stats", headers=ADMIN).json()
    assert stats["environments"]["prod"]["count"] == 2
    assert stats["environments"]["prod"]["variations"] == {"on": 1, "off": 1}
    assert stats["environments"]["prod"]["last_evaluated_at"] == T0.isoformat()
    assert stats["environments"]["dev"]["count"] == 0


def test_sdk_flags_etag_and_304(client):
    server, _ = setup_project(client, BD_RULE)
    auth = {"Authorization": f"Bearer {server}"}
    r = client.get("/v1/sdk/flags", headers=auth)
    assert r.status_code == 200
    etag = r.headers["ETag"]
    body = r.json()
    assert body["environment"] == "prod" and body["flags"]["new-checkout"]["rules"] == BD_RULE
    assert body["flags"]["new-checkout"]["salt"]
    again = client.get("/v1/sdk/flags", headers={**auth, "If-None-Match": etag})
    assert again.status_code == 304 and again.headers["ETag"] == etag and again.content == b""
    weak_list = client.get("/v1/sdk/flags", headers={**auth, "If-None-Match": f'"other", {etag}'})
    assert weak_list.status_code == 304
    client.post("/v1/projects/shop/flags/new-checkout/environments/prod/kill", headers=ADMIN)
    changed = client.get("/v1/sdk/flags", headers={**auth, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["ETag"] != etag
    assert changed.json()["flags"]["new-checkout"]["enabled"] is False
    assert int(changed.headers["X-Flags-Version"]) > int(r.headers["X-Flags-Version"])


def test_client_key_gets_evaluated_payload_only(client):
    _, web = setup_project(client, BD_RULE)
    auth = {"Authorization": f"Bearer {web}"}
    r = client.get("/v1/sdk/flags", headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "context_required"
    r = client.get(
        "/v1/sdk/flags",
        params={"context": b64({"key": "u1", "attributes": {"country": "BD"}})},
        headers=auth,
    )
    assert r.status_code == 200
    flag = r.json()["flags"]["new-checkout"]
    assert flag == {
        "value": True,
        "variation": "on",
        "reason": {"kind": "RULE_MATCH", "rule_id": "bd", "rule_index": 0},
        "version": 2,
    }
    etag_bd = r.headers["ETag"]
    other = client.get(
        "/v1/sdk/flags",
        params={"context": b64({"key": "u1", "attributes": {"country": "IN"}})},
        headers=auth,
    )
    assert (
        other.json()["flags"]["new-checkout"]["value"] is False and other.headers["ETag"] != etag_bd
    )
    assert client.get("/v1/sdk/flags", params={"context": "!!!"}, headers=auth).status_code == 400
    cached = client.get(
        "/v1/sdk/flags",
        params={"context": b64({"key": "u1", "attributes": {"country": "BD"}})},
        headers={**auth, "If-None-Match": etag_bd},
    )
    assert cached.status_code == 304


def test_sse_stream_sends_put_then_update(client, clock):
    server, _ = setup_project(client)
    auth = {"Authorization": f"Bearer {server}"}
    with client.stream("GET", "/v1/sdk/stream", params={"max_events": 1}, headers=auth) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    assert text.startswith("id: ") and "event: put\n" in text
    data = json.loads(text.split("data: ", 1)[1].split("\n", 1)[0])
    assert data["environment"] == "prod" and "new-checkout" in data["flags"]
    etag = client.get("/v1/sdk/flags", headers=auth).headers["ETag"].strip('"')
    assert text.splitlines()[0] == f"id: {etag}"


def test_sse_client_key_requires_context(client):
    _, web = setup_project(client)
    r = client.get("/v1/sdk/stream", headers={"Authorization": f"Bearer {web}"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "context_required"


def test_segments_schedule_audit_and_webhooks_via_api(client, receiver):
    server, _ = setup_project(client)
    r = client.post(
        "/v1/projects/shop/webhooks",
        json={"url": "https://ci.example.com/hook", "secret": "s3cr3t-s3cr3t-s3cr3t"},
        headers=ADMIN,
    )
    assert r.status_code == 201 and r.json()["secret"] == "s3cr3t-s3cr3t-s3cr3t"
    assert "secret" not in client.get("/v1/projects/shop/webhooks", headers=ADMIN).json()[0]
    r = client.post(
        "/v1/projects/shop/segments",
        json={"key": "beta", "name": "Beta testers", "included": ["u1"]},
        headers=ADMIN,
    )
    assert r.status_code == 201 and r.json()["version"] == 1
    r = client.patch(
        "/v1/projects/shop/flags/new-checkout/environments/prod",
        json={
            "rules": [
                {
                    "id": "beta",
                    "clauses": [{"op": "in_segment", "values": ["beta"]}],
                    "variation": "on",
                }
            ]
        },
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    assert receiver.requests[-1].headers["X-Flags-Event"] == "flag.config_updated"
    r = client.post(
        "/v1/evaluate",
        json={"context": {"key": "u1"}, "flag": "new-checkout"},
        headers={"Authorization": f"Bearer {server}"},
    )
    assert r.json()["value"] is True and r.json()["reason"]["rule_id"] == "beta"
    assert client.delete("/v1/projects/shop/segments/beta", headers=ADMIN).status_code == 409
    r = client.post(
        "/v1/projects/shop/flags/new-checkout/environments/prod/schedule",
        json={
            "scheduled_at": (T0 + timedelta(hours=3)).isoformat(),
            "changes": {"enabled": False},
            "comment": "end of promo",
        },
        headers=ADMIN,
    )
    assert r.status_code == 201 and r.json()["status"] == "pending"
    assert client.post("/v1/scheduled-changes/apply", headers=ADMIN).json() == []
    audit = client.get("/v1/projects/shop/audit?entity_type=flag", headers=ADMIN).json()
    assert audit[0]["action"] == "flag.change_scheduled" and audit[0]["actor"] == "akib"
    assert {a["action"] for a in audit} >= {
        "flag.created",
        "flag.config_updated",
        "flag.change_scheduled",
    }
    pending = client.get(
        "/v1/projects/shop/flags/new-checkout/schedule?status=pending", headers=ADMIN
    ).json()
    assert (
        client.delete(
            f"/v1/projects/shop/flags/new-checkout/schedule/{pending[0]['id']}", headers=ADMIN
        ).json()["status"]
        == "canceled"
    )


def test_scheduled_change_applied_via_api_when_clock_advances(client, clock):
    server, _ = setup_project(client)
    client.post(
        "/v1/projects/shop/flags/new-checkout/environments/prod/schedule",
        json={"scheduled_at": (T0 + timedelta(hours=1)).isoformat(), "changes": {"enabled": False}},
        headers=ADMIN,
    )
    clock.advance(hours=1)
    applied = client.post("/v1/scheduled-changes/apply", headers=ADMIN).json()
    assert len(applied) == 1 and applied[0]["status"] == "applied"
    r = client.post(
        "/v1/evaluate",
        json={"context": {"key": "u1"}, "flag": "new-checkout"},
        headers={"Authorization": f"Bearer {server}"},
    )
    assert r.json()["reason"] == {"kind": "OFF"}


def test_sdk_key_listing_and_revocation(client):
    server, _ = setup_project(client)
    keys = client.get("/v1/projects/shop/environments/prod/sdk-keys", headers=ADMIN).json()
    assert len(keys) == 2 and all("key" not in k for k in keys)
    assert keys[0]["prefix"] == server[:12]
    revoked = client.delete(
        f"/v1/projects/shop/environments/prod/sdk-keys/{keys[0]['id']}", headers=ADMIN
    ).json()
    assert revoked["revoked_at"] is not None
    assert (
        client.get("/v1/sdk/flags", headers={"Authorization": f"Bearer {server}"}).status_code
        == 401
    )
