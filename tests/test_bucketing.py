import pytest

from feature_flags.core.bucketing import (
    WEIGHT_TOTAL,
    bucket,
    holdout_seed,
    in_holdout,
    pick_variation,
    validate_holdout,
    validate_rollout,
)
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


USERS = [Context(f"user-{i}") for i in range(50_000)]


def held_out(holdout, seed, users=USERS, bucket_by="key"):
    return {u.key for u in users if in_holdout(holdout, seed, u, bucket_by)}


def test_holdout_ratio_within_one_percent_over_50k_keys():
    for holdout in (1_000, 5_000, 20_000):
        share = len(held_out(holdout, holdout_seed("exp", "salt"))) / len(USERS)
        assert abs(share - holdout / WEIGHT_TOTAL) < 0.01


def test_holdout_is_sticky_and_grows_monotonically():
    seed = holdout_seed("exp", "salt")
    five, ten = held_out(5_000, seed), held_out(10_000, seed)
    assert five < ten  # widening the holdout keeps everyone already held out
    assert held_out(5_000, seed) == five
    assert held_out(0, seed) == set()


def test_holdout_is_independent_of_the_variation_split():
    seed = holdout_seed("exp", "salt")
    holdout = held_out(10_000, seed)
    in_on = {u.key for u in USERS if pick_variation(ROLLOUT, "exp", "salt", u) == "on"}
    # the holdout cuts both arms at roughly the holdout rate instead of eating one of them
    assert abs(len(holdout & in_on) / len(in_on) - 0.10) < 0.01
    assert abs(len(holdout - in_on) / (len(USERS) - len(in_on)) - 0.10) < 0.01


def test_holdout_seed_is_per_experiment():
    shared = holdout_seed("flag-a", "s1", "q4-checkout")
    assert shared == holdout_seed("flag-b", "s2", "q4-checkout")
    a = held_out(10_000, holdout_seed("flag-a", "s1"))
    b = held_out(10_000, holdout_seed("flag-b", "s2"))
    other = held_out(10_000, holdout_seed("flag-a", "s1", "q1-search"))
    # unrelated experiments overlap only by chance (~10% of 10%)
    assert len(a & b) / len(a) < 0.15
    assert len(a & other) / len(a) < 0.15
    # and the holdout point is not the variation point
    assert bucket("holdout", shared, USERS[0]) != bucket("flag-a", "s1", USERS[0])


def test_holdout_honours_bucket_by():
    team = [Context(f"u{i}", {"team": "alpha"}) for i in range(200)]
    seed = holdout_seed("exp", "salt")
    results = {in_holdout(50_000, seed, c, bucket_by="team") for c in team}
    assert len(results) == 1


def test_holdout_validation():
    validate_holdout(0)
    validate_holdout(WEIGHT_TOTAL - 1)
    for bad in (-1, WEIGHT_TOTAL, 2.5, "5000", True, None):
        with pytest.raises(ValueError):
            validate_holdout(bad)
