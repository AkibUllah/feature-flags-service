import json
from datetime import timedelta

import pytest

from feature_flags.core.clauses import Context
from feature_flags.core.signing import HEADER, verify
from feature_flags.services import audit, flags, payloads, projects, scheduled, segments, webhooks
from feature_flags.services.bus import ChangeEvent
from feature_flags.services.errors import Conflict, DomainError, NotFound, Unauthorized
from tests.conftest import BOOL_VARIATIONS, T0, enable


def test_project_gets_default_environments_and_audit(session, project):
    assert [e.key for e in project.environments] == ["dev", "staging", "prod"]
    with pytest.raises(Conflict):
        projects.create_project(session, key="shop", name="x", now=T0, actor="akib")
    entries = audit.list_entries(session, project.id)
    assert entries[0].action == "project.created" and entries[0].actor == "akib"
    env = projects.create_environment(session, project, key="qa", name="QA", now=T0, actor="akib")
    assert projects.get_environment(session, project, "qa") is env
    with pytest.raises(NotFound):
        projects.get_environment(session, project, "nope")


def test_sdk_keys_are_hashed_and_revocable(session, prod, clock):
    row, plaintext = projects.create_sdk_key(
        session, prod, kind="server", name="backend", now=T0, actor="akib"
    )
    assert plaintext.startswith("srv-") and row.prefix == plaintext[:12]
    assert row.hashed_secret != plaintext and plaintext not in row.hashed_secret
    found = projects.authenticate_sdk_key(session, plaintext, T0 + timedelta(minutes=1))
    assert found.id == row.id and found.last_used_at == T0 + timedelta(minutes=1)
    with pytest.raises(Unauthorized):
        projects.authenticate_sdk_key(session, "srv-wrong", T0)
    with pytest.raises(Unauthorized):
        projects.authenticate_sdk_key(session, None, T0)
    projects.revoke_sdk_key(session, prod, row.id, now=T0, actor="akib")
    with pytest.raises(Unauthorized):
        projects.authenticate_sdk_key(session, plaintext, T0)
    with pytest.raises(ValueError):
        projects.create_sdk_key(session, prod, kind="mobile", name="x", now=T0, actor="akib")


def test_create_flag_defaults_and_validation(session, ctx, project, bool_flag, prod):
    config = flags.get_config(bool_flag, prod)
    assert config.enabled is False and config.off_variation == "off"
    assert config.fallthrough == {"variation": "on"}
    assert len(bool_flag.configs) == 3
    with pytest.raises(Conflict):
        flags.create_flag(
            session,
            ctx,
            project,
            key="new-checkout",
            name="dup",
            kind="boolean",
            variations=BOOL_VARIATIONS,
            now=T0,
            actor="a",
        )
    with pytest.raises(DomainError, match="exactly one true"):
        flags.create_flag(
            session,
            ctx,
            project,
            key="bad",
            name="b",
            kind="boolean",
            variations=[{"key": "a", "value": True}, {"key": "b", "value": True}],
            now=T0,
            actor="a",
        )
    with pytest.raises(DomainError, match="must match"):
        flags.create_flag(
            session,
            ctx,
            project,
            key="Bad Key",
            name="b",
            kind="string",
            variations=[{"key": "a", "value": "x"}, {"key": "b", "value": "y"}],
            now=T0,
            actor="a",
        )
    with pytest.raises(DomainError, match="numeric"):
        flags.create_flag(
            session,
            ctx,
            project,
            key="n",
            name="n",
            kind="number",
            variations=[{"key": "a", "value": 1}, {"key": "b", "value": "2"}],
            now=T0,
            actor="a",
        )


def test_update_config_bumps_versions_audits_and_invalidates_cache(
    session, ctx, project, bool_flag, prod
):
    before_env_version = prod.version
    payload, etag = payloads.cached_payload(session, ctx.kv, prod, 300)
    assert payload["flags"]["new-checkout"]["enabled"] is False
    config = enable(
        session,
        ctx,
        project,
        bool_flag,
        prod,
        rules=[
            {
                "id": "bd",
                "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
                "variation": "on",
            }
        ],
    )
    assert config.version == 2 and prod.version == before_env_version + 1
    new_payload, new_etag = payloads.cached_payload(session, ctx.kv, prod, 300)
    assert new_payload["flags"]["new-checkout"]["enabled"] is True and new_etag != etag
    entry = audit.list_entries(session, project.id, entity_type="flag", entity_id="new-checkout")[0]
    assert entry.action == "flag.config_updated" and entry.environment_key == "prod"
    assert (
        entry.before["enabled"] is False
        and entry.after["enabled"] is True
        and entry.after["version"] == 2
    )
    # a no-op patch changes nothing
    same = flags.update_config(
        session, ctx, project, bool_flag, prod, {"enabled": True}, now=T0, actor="akib"
    )
    assert same.version == 2


def test_config_validation_rejects_broken_targeting(session, ctx, project, bool_flag, prod):
    cases = [
        ({"fallthrough": {"variation": "nope"}}, "unknown variation"),
        ({"off_variation": "nope"}, "off_variation"),
        ({"rules": [{"id": "r", "clauses": [], "variation": "on"}]}, "at least one clause"),
        (
            {
                "rules": [
                    {
                        "id": "r",
                        "clauses": [{"attribute": "a", "op": "like", "values": ["x"]}],
                        "variation": "on",
                    }
                ]
            },
            "unsupported operator",
        ),
        (
            {
                "rules": [
                    {
                        "id": "r",
                        "clauses": [{"attribute": "a", "op": "regex", "values": ["("]}],
                        "variation": "on",
                    }
                ]
            },
            "invalid regex",
        ),
        (
            {
                "rules": [
                    {
                        "id": "r",
                        "clauses": [{"op": "in_segment", "values": ["ghost"]}],
                        "variation": "on",
                    }
                ]
            },
            "unknown segments",
        ),
        (
            {
                "rules": [
                    {
                        "id": "r",
                        "clauses": [{"attribute": "a", "op": "eq", "values": ["x"]}],
                        "variation": "on",
                        "rollout": [{"variation": "on", "weight": 100000}],
                    }
                ]
            },
            "exactly one",
        ),
        (
            {
                "fallthrough": {
                    "rollout": [
                        {"variation": "on", "weight": 60000},
                        {"variation": "off", "weight": 60000},
                    ]
                }
            },
            "sum to",
        ),
        (
            {
                "rules": [
                    {
                        "id": "r",
                        "clauses": [{"attribute": "a", "op": "eq", "values": ["x"]}],
                        "variation": "on",
                    },
                    {
                        "id": "r",
                        "clauses": [{"attribute": "a", "op": "eq", "values": ["x"]}],
                        "variation": "on",
                    },
                ]
            },
            "unique id",
        ),
    ]
    for changes, message in cases:
        with pytest.raises(DomainError, match=message):
            flags.update_config(
                session, ctx, project, bool_flag, prod, changes, now=T0, actor="akib"
            )


def test_kill_switch_and_restore(session, ctx, project, bool_flag, prod):
    enable(session, ctx, project, bool_flag, prod)
    killed = flags.set_enabled(
        session, ctx, project, bool_flag, prod, False, now=T0, actor="oncall"
    )
    assert killed.enabled is False and killed.fallthrough == {"variation": "on"}  # rules kept
    assert audit.list_entries(session, project.id, entity_type="flag")[0].action == "flag.killed"
    assert flags.set_enabled(
        session, ctx, project, bool_flag, prod, True, now=T0, actor="oncall"
    ).enabled


def test_segments_touch_every_environment_and_block_deletion_when_used(
    session, ctx, project, bool_flag, prod
):
    versions = {e.key: e.version for e in project.environments}
    seg = segments.create_segment(
        session,
        ctx,
        project,
        key="beta",
        name="Beta",
        included=["vip"],
        now=T0,
        actor="akib",
        rules=[{"clauses": [{"attribute": "plan", "op": "eq", "values": ["pro"]}]}],
    )
    assert all(e.version == versions[e.key] + 1 for e in project.environments)
    payload, _ = payloads.cached_payload(session, ctx.kv, prod, 300)
    assert payload["segments"]["beta"]["included"] == ["vip"]
    enable(
        session,
        ctx,
        project,
        bool_flag,
        prod,
        rules=[
            {"id": "b", "clauses": [{"op": "in_segment", "values": ["beta"]}], "variation": "on"}
        ],
    )
    with pytest.raises(Conflict, match="is used by flag"):
        segments.delete_segment(session, ctx, project, seg, now=T0, actor="akib")
    segments.update_segment(session, ctx, project, seg, {"excluded": ["vip"]}, now=T0, actor="akib")
    assert seg.version == 2
    with pytest.raises(DomainError, match="cannot reference"):
        segments.update_segment(
            session,
            ctx,
            project,
            seg,
            {"rules": [{"clauses": [{"op": "in_segment", "values": ["x"]}]}]},
            now=T0,
            actor="akib",
        )


def test_scheduled_change_applies_only_when_due(session, ctx, project, bool_flag, prod, clock):
    at = T0 + timedelta(hours=2)
    change = scheduled.schedule(
        session,
        project,
        bool_flag,
        prod,
        scheduled_at=at,
        changes={"enabled": True},
        now=T0,
        actor="akib",
        comment="launch",
    )
    assert change.status == "pending"
    with pytest.raises(DomainError, match="future"):
        scheduled.schedule(
            session,
            project,
            bool_flag,
            prod,
            scheduled_at=T0,
            changes={"enabled": True},
            now=T0,
            actor="akib",
        )
    with pytest.raises(DomainError, match="unknown variation"):
        scheduled.schedule(
            session,
            project,
            bool_flag,
            prod,
            scheduled_at=at,
            changes={"fallthrough": {"variation": "zzz"}},
            now=T0,
            actor="akib",
        )
    assert scheduled.apply_due(session, ctx, T0 + timedelta(hours=1)) == []
    assert flags.get_config(bool_flag, prod).enabled is False
    applied = scheduled.apply_due(session, ctx, at)
    assert (
        [c.id for c in applied] == [change.id]
        and change.status == "applied"
        and change.applied_at == at
    )
    assert flags.get_config(bool_flag, prod).enabled is True
    assert audit.list_entries(session, project.id, entity_type="flag")[0].actor == "scheduler:akib"
    assert scheduled.apply_due(session, ctx, at + timedelta(hours=1)) == []  # not applied twice
    later = scheduled.schedule(
        session,
        project,
        bool_flag,
        prod,
        scheduled_at=at + timedelta(days=1),
        changes={"enabled": False},
        now=at,
        actor="akib",
    )
    scheduled.cancel(session, project, bool_flag, later.id, now=at, actor="akib")
    assert later.status == "canceled"
    with pytest.raises(DomainError, match="not_pending|is canceled"):
        scheduled.cancel(session, project, bool_flag, later.id, now=at, actor="akib")


def test_webhook_notified_and_signed_on_config_change(
    session, ctx, project, bool_flag, prod, receiver
):
    webhooks.create_endpoint(
        session, project, url="https://ci.example.com/flags", secret="s3cr3t-s3cr3t-s3cr3t", now=T0
    )
    enable(session, ctx, project, bool_flag, prod)
    (req,) = receiver.requests
    verify("s3cr3t-s3cr3t-s3cr3t", req.content, req.headers[HEADER], now=int(T0.timestamp()))
    body = json.loads(req.content)
    assert body["type"] == "flag.config_updated" and body["data"]["flag"] == "new-checkout"
    assert body["data"]["before"]["enabled"] is False and body["data"]["after"]["enabled"] is True
    endpoint = webhooks.list_endpoints(session, project)[0]
    assert endpoint.last_status_code == 200 and endpoint.last_error is None
    receiver.status = 503
    flags.set_enabled(session, ctx, project, bool_flag, prod, False, now=T0, actor="a")
    assert endpoint.last_status_code == 503 and endpoint.last_error == "HTTP 503"


def test_bus_publishes_on_changes(session, ctx, project, bool_flag, prod):
    events: list[ChangeEvent] = []
    original = ctx.bus.publish
    ctx.bus.publish = lambda e: (events.append(e), original(e))[1]
    enable(session, ctx, project, bool_flag, prod)
    assert (
        events[-1].kind == "flag"
        and events[-1].entity_key == "new-checkout"
        and events[-1].version == prod.version
    )
    flags.archive_flag(session, ctx, project, bool_flag, now=T0, actor="a")
    assert len(events) == 4  # 1 config change + 3 environments on archive
    payload, _ = payloads.cached_payload(session, ctx.kv, prod, 300)
    assert payload["flags"] == {}


def test_client_payload_hides_rules_and_etag_depends_on_context(
    session, ctx, project, bool_flag, prod
):
    enable(
        session,
        ctx,
        project,
        bool_flag,
        prod,
        rules=[
            {
                "id": "bd",
                "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
                "variation": "on",
            }
        ],
        fallthrough={"variation": "off"},
    )
    payload, etag = payloads.cached_payload(session, ctx.kv, prod, 300)
    bd = payloads.client_payload(payload, Context("u1", {"country": "BD"}))
    other = payloads.client_payload(payload, Context("u1", {"country": "IN"}))
    assert (
        bd["flags"]["new-checkout"]["value"] is True
        and other["flags"]["new-checkout"]["value"] is False
    )
    assert "rules" not in bd["flags"]["new-checkout"]
    assert payloads.client_etag(etag, Context("u1", {"country": "BD"})) != payloads.client_etag(
        etag, Context("u1", {"country": "IN"})
    )
    assert payloads.client_etag(etag, Context("u1", {"country": "BD"})) == payloads.client_etag(
        etag, Context("u1", {"country": "BD"})
    )
