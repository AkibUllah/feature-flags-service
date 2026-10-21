import asyncio

import pytest

from feature_flags.api.sdk import format_sse, sse_events
from feature_flags.services.bus import ChangeEvent, EventBus
from feature_flags.services.context import AppContext


class Env:
    id = "env_1"


def make_ctx(settings):
    return AppContext(settings=settings)


def test_format_sse():
    assert format_sse("put", {"a": 1}, event_id="abc") == 'id: abc\nevent: put\ndata: {"a":1}\n\n'
    assert format_sse("ping", "x") == "event: ping\ndata: x\n\n"


@pytest.mark.asyncio
async def test_stream_emits_initial_put_then_update_and_heartbeat(settings):
    ctx = make_ctx(settings)
    state = {"version": 1}

    def load():
        return {"version": state["version"], "flags": {}}, f'"etag-{state["version"]}"'

    gen = sse_events(ctx, Env(), load, heartbeat_seconds=0.05)
    first = await gen.__anext__()
    assert first == 'id: etag-1\nevent: put\ndata: {"version":1,"flags":{}}\n\n'
    heartbeat = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert heartbeat == ": keep-alive\n\n"
    assert ctx.bus.subscriber_count("env_1") == 1

    state["version"] = 2
    ctx.bus.publish(ChangeEvent("env_1", 2, "flag", "x"))
    ctx.bus.publish(ChangeEvent("env_1", 2, "flag", "y"))  # coalesced into one rebuild
    update = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert update.startswith("id: etag-2\nevent: put\n")
    ctx.bus.publish(ChangeEvent("env_1", 1, "flag", "stale"))  # older version: ignored
    assert await asyncio.wait_for(gen.__anext__(), timeout=1) == ": keep-alive\n\n"
    await gen.aclose()
    assert ctx.bus.subscriber_count("env_1") == 0


@pytest.mark.asyncio
async def test_stream_stops_at_max_events_and_on_disconnect(settings):
    ctx = make_ctx(settings)

    def load():
        return {"version": 1, "flags": {}}, '"e"'

    events = [e async for e in sse_events(ctx, Env(), load, heartbeat_seconds=1, max_events=1)]
    assert len(events) == 1

    async def gone():
        return True

    events = [e async for e in sse_events(ctx, Env(), load, heartbeat_seconds=1, disconnected=gone)]
    assert len(events) == 1
    assert ctx.bus.subscriber_count() == 0


def test_bus_fanout_and_full_queue():
    bus = EventBus()
    with bus.subscribe("env") as q1, bus.subscribe("env") as q2, bus.subscribe("other") as q3:
        assert bus.publish(ChangeEvent("env", 1, "flag")) == 2
        assert q1.qsize() == 1 and q2.qsize() == 1 and q3.qsize() == 0
        for _ in range(300):
            bus.publish(ChangeEvent("env", 1, "flag"))
        assert q1.full()
    assert bus.subscriber_count() == 0
