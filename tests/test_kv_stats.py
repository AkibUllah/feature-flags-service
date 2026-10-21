import fakeredis
import pytest

from feature_flags.kv import MemoryKV, RedisKV, build_kv
from feature_flags.services import stats
from tests.conftest import T0


@pytest.fixture(params=["memory", "redis"])
def kv(request):
    return MemoryKV() if request.param == "memory" else RedisKV(fakeredis.FakeRedis())


def test_kv_basic_operations(kv):
    assert kv.get("missing") is None
    kv.set("a", "1")
    kv.set("b", "2", ttl_seconds=60)
    assert kv.get("a") == "1" and kv.get("b") == "2"
    assert kv.hincrby("h", "count") == 1 and kv.hincrby("h", "count", 4) == 5
    kv.hset("h", "last", "x")
    assert kv.hgetall("h") == {"count": "5", "last": "x"}
    assert kv.keys("*") == ["a", "b", "h"]
    kv.delete("a", "h")
    assert kv.get("a") is None and kv.hgetall("h") == {}
    assert kv.ping()


def test_memory_kv_expiry():
    clock = {"t": 0.0}
    kv = MemoryKV(clock=lambda: clock["t"])
    kv.set("k", "v", ttl_seconds=10)
    clock["t"] = 9.9
    assert kv.get("k") == "v"
    clock["t"] = 10.0
    assert kv.get("k") is None


def test_build_kv_without_url_is_memory():
    assert build_kv(None).name == "memory"
    assert build_kv("").name == "memory"


def test_stats_record_and_read(kv):
    stats.record(kv, "env_1", "new-checkout", "on", "RULE_MATCH", T0)
    stats.record(kv, "env_1", "new-checkout", "off", "FALLTHROUGH", T0)
    stats.record(kv, "env_1", "new-checkout", "on", "RULE_MATCH", T0)
    out = stats.read(kv, "env_1", "new-checkout")
    assert out["count"] == 3
    assert out["variations"] == {"on": 2, "off": 1}
    assert out["reasons"] == {"RULE_MATCH": 2, "FALLTHROUGH": 1}
    assert out["last_evaluated_at"] == T0.isoformat()
    assert stats.read(kv, "env_1", "other")["count"] == 0
    stats.reset(kv, "env_1", "new-checkout")
    assert stats.read(kv, "env_1", "new-checkout")["count"] == 0
