import pytest

from feature_flags.core.bucketing import WEIGHT_TOTAL, bucket, pick_variation, validate_rollout
from feature_flags.core.clauses import Context

ROLLOUT = [{"variation": "on", "weight": 50_000}, {"variation": "off", "weight": 50_000}]


def test_same_context_always_same_bucket():
    ctx = Context("user_42")
    first = bucket("new-checkout", "salt", ctx)
    assert all(bucket("new-checkout", "salt", Context("user_42")) == first for _ in range(100))
    assert 0 <= first < 1


def test_different_flags_and_salts_decorrelate_users():
    users = [Context(f"user_{i}") for i in range(2_000)]
    a = [pick_variation(ROLLOUT, "flag-a", "s", u) for u in users]
    b = [pick_variation(ROLLOUT, "flag-b", "s", u) for u in users]
    c = [pick_variation(ROLLOUT, "flag-a", "other-salt", u) for u in users]
    assert 0.35 < sum(x == y for x, y in zip(a, b, strict=True)) / len(users) < 0.65
    assert 0.35 < sum(x == y for x, y in zip(a, c, strict=True)) / len(users) < 0.65


def test_distribution_within_two_percent_over_100k_keys():
    rollout = [
        {"variation": "a", "weight": 10_000},
        {"variation": "b", "weight": 30_000},
        {"variation": "c", "weight": 60_000},
    ]
    counts = {"a": 0, "b": 0, "c": 0}
    n = 100_000
    for i in range(n):
        counts[pick_variation(rollout, "exp", "salt", Context(f"user-{i}"))] += 1
    assert abs(counts["a"] / n - 0.10) < 0.02
    assert abs(counts["b"] / n - 0.30) < 0.02
    assert abs(counts["c"] / n - 0.60) < 0.02


def test_bucket_by_attribute_groups_contexts():
    rollout = [{"variation": "on", "weight": 30_000}, {"variation": "off", "weight": 70_000}]
    team_a = [Context(f"u{i}", {"team": "alpha"}) for i in range(50)]
    results = {pick_variation(rollout, "f", "s", c, bucket_by="team") for c in team_a}
    assert len(results) == 1  # everyone in the same team lands together
    # missing attribute falls back to the key
    assert bucket("f", "s", Context("u1"), bucket_by="team") == bucket("f", "s", Context("u1"))


def test_rollout_validation():
    validate_rollout(ROLLOUT)
    with pytest.raises(ValueError):
        validate_rollout([])
    with pytest.raises(ValueError):
        validate_rollout([{"variation": "on", "weight": WEIGHT_TOTAL - 1}])
    with pytest.raises(ValueError):
        validate_rollout(
            [{"variation": "on", "weight": -1}, {"variation": "off", "weight": WEIGHT_TOTAL + 1}]
        )
