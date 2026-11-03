from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import (
    MAX_PREREQUISITE_DEPTH,
    evaluate,
    evaluate_all,
    segment_matches,
)


def flag(**overrides):
    base = {
        "key": "new-checkout",
        "kind": "boolean",
        "version": 7,
        "enabled": True,
        "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
        "off_variation": "off",
        "targets": [],
        "rules": [],
        "fallthrough": {"variation": "off"},
        "salt": "abc",
    }
    base.update(overrides)
    return base


def test_kill_switch_serves_off_variation():
    r = evaluate(flag(enabled=False, fallthrough={"variation": "on"}), Context("u1"))
    assert (r.value, r.variation, r.reason.kind, r.version) == (False, "off", "OFF", 7)
    assert r.as_dict()["reason"] == {"kind": "OFF"}


def test_individual_targets_beat_rules():
    f = flag(
        targets=[{"variation": "off", "keys": ["u1"]}],
        rules=[{"id": "all", "clauses": [{"attribute": "key", "op": "exists"}], "variation": "on"}],
    )
    assert evaluate(f, Context("u1")).reason.kind == "TARGET_MATCH"
    assert evaluate(f, Context("u1")).value is False
    assert evaluate(f, Context("u2")).reason.kind == "RULE_MATCH"


def test_first_matching_rule_wins_with_index_and_id():
    f = flag(
        rules=[
            {
                "id": "bd",
                "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
                "variation": "off",
            },
            {
                "id": "pro",
                "clauses": [{"attribute": "plan", "op": "eq", "values": ["pro"]}],
                "variation": "on",
            },
        ]
    )
    r = evaluate(f, Context("u1", {"country": "BD", "plan": "pro"}))
    assert (r.variation, r.reason.rule_id, r.reason.rule_index) == ("off", "bd", 0)
    r = evaluate(f, Context("u2", {"country": "IN", "plan": "pro"}))
    assert (r.variation, r.reason.rule_id, r.reason.rule_index) == ("on", "pro", 1)
    r = evaluate(f, Context("u3", {"country": "IN"}))
    assert r.reason.kind == "FALLTHROUGH" and r.variation == "off"


def test_rule_clauses_are_anded():
    f = flag(
        rules=[
            {
                "id": "r",
                "clauses": [
                    {"attribute": "country", "op": "eq", "values": ["BD"]},
                    {"attribute": "plan", "op": "in", "values": ["pro", "team"]},
                ],
                "variation": "on",
            }
        ]
    )
    assert evaluate(f, Context("u", {"country": "BD", "plan": "free"})).variation == "off"
    assert evaluate(f, Context("u", {"country": "BD", "plan": "team"})).variation == "on"


def test_segments_included_excluded_and_rules():
    segments = {
        "beta": {
            "key": "beta",
            "included": ["vip"],
            "excluded": ["banned"],
            "rules": [
                {
                    "clauses": [
                        {
                            "attribute": "email",
                            "op": "ends_with",
                            "values": ["@dhakagrocers.example"],
                        }
                    ]
                }
            ],
        }
    }
    assert segment_matches(segments["beta"], Context("vip"))
    assert not segment_matches(
        segments["beta"], Context("banned", {"email": "x@dhakagrocers.example"})
    )
    assert segment_matches(segments["beta"], Context("u", {"email": "x@dhakagrocers.example"}))
    f = flag(
        rules=[
            {"id": "beta", "clauses": [{"op": "in_segment", "values": ["beta"]}], "variation": "on"}
        ]
    )
    assert evaluate(f, Context("vip"), segments).variation == "on"
    assert evaluate(f, Context("someone"), segments).variation == "off"
    assert evaluate(f, Context("vip"), {}).variation == "off"  # unknown segment never matches
    negated = flag(
        rules=[
            {
                "id": "nb",
                "clauses": [{"op": "in_segment", "values": ["beta"], "negate": True}],
                "variation": "on",
            }
        ]
    )
    assert evaluate(negated, Context("someone"), segments).variation == "on"


def test_fallthrough_rollout_is_sticky_and_reported():
    f = flag(
        fallthrough={
            "rollout": [
                {"variation": "on", "weight": 50_000},
                {"variation": "off", "weight": 50_000},
            ]
        }
    )
    first = evaluate(f, Context("user_7"))
    assert first.reason.kind == "FALLTHROUGH" and first.reason.in_rollout is True
    assert all(evaluate(f, Context("user_7")).variation == first.variation for _ in range(20))
    seen = {evaluate(f, Context(f"user_{i}")).variation for i in range(200)}
    assert seen == {"on", "off"}


def test_rule_rollout_with_bucket_by():
    f = flag(
        rules=[
            {
                "id": "r",
                "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
                "rollout": [{"variation": "on", "weight": 100_000}],
                "bucket_by": "company",
            }
        ]
    )
    r = evaluate(f, Context("u", {"country": "BD", "company": "acme"}))
    assert r.variation == "on" and r.reason.in_rollout


def test_multivariate_json_and_string_values():
    f = flag(
        kind="json",
        variations=[
            {"key": "v1", "value": {"layout": "grid", "cols": 3}},
            {"key": "v2", "value": {"layout": "list"}},
            {"key": "none", "value": None},
        ],
        off_variation="none",
        fallthrough={"variation": "v1"},
    )
    assert evaluate(f, Context("u")).value == {"layout": "grid", "cols": 3}
    assert evaluate({**f, "enabled": False}, Context("u")).value is None


def test_structural_errors_fall_back_to_off_with_error_reason():
    broken = flag(fallthrough={"variation": "does-not-exist"})
    r = evaluate(broken, Context("u"))
    assert (r.variation, r.value, r.reason.kind) == ("off", False, "ERROR")
    assert "does-not-exist" in r.reason.error
    no_target = flag(rules=[{"id": "r", "clauses": [{"attribute": "key", "op": "exists"}]}])
    assert evaluate(no_target, Context("u")).reason.kind == "ERROR"
    hopeless = flag(off_variation="nope", fallthrough={"variation": "nope"})
    r = evaluate(hopeless, Context("u"))
    assert r.variation is None and r.value is None and r.reason.kind == "ERROR"


def test_evaluate_all():
    flags = {"a": flag(key="a"), "b": flag(key="b", enabled=False)}
    results = evaluate_all(flags, Context("u"))
    assert {k: r.reason.kind for k, r in results.items()} == {"a": "FALLTHROUGH", "b": "OFF"}


# --- prerequisites --------------------------------------------------------------------------


def bd_rule(variation="on"):
    return {
        "id": "bd",
        "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
        "variation": variation,
    }


def prereq_flags():
    """checkout-v2 needs new-checkout == on; express-pay needs checkout-v2 == on."""
    return {
        "new-checkout": flag(rules=[bd_rule()]),
        "checkout-v2": flag(
            key="checkout-v2",
            prerequisites=[{"flag_key": "new-checkout", "variation": "on"}],
            fallthrough={"variation": "on"},
        ),
        "express-pay": flag(
            key="express-pay",
            prerequisites=[{"flag_key": "checkout-v2", "variation": "on"}],
            fallthrough={"variation": "on"},
        ),
    }


def test_prerequisite_met_lets_the_flag_evaluate_normally():
    flags = prereq_flags()
    r = evaluate(flags["checkout-v2"], Context("u", {"country": "BD"}), {}, flags)
    assert (r.variation, r.reason.kind) == ("on", "FALLTHROUGH")


def test_prerequisite_not_met_serves_off_with_reason():
    flags = prereq_flags()
    r = evaluate(flags["checkout-v2"], Context("u", {"country": "IN"}), {}, flags)
    assert (r.variation, r.value, r.reason.kind) == ("off", False, "PREREQUISITE_FAILED")
    assert r.as_dict()["reason"] == {
        "kind": "PREREQUISITE_FAILED",
        "prerequisite_key": "new-checkout",
    }


def test_prerequisite_chain_reports_the_direct_prerequisite():
    flags = prereq_flags()
    assert evaluate(flags["express-pay"], Context("u", {"country": "BD"}), {}, flags).value is True
    r = evaluate(flags["express-pay"], Context("u", {"country": "IN"}), {}, flags)
    assert (r.reason.kind, r.reason.prerequisite_key) == ("PREREQUISITE_FAILED", "checkout-v2")


def test_prerequisite_that_is_switched_off_is_not_met_even_on_its_off_variation():
    flags = prereq_flags()
    flags["new-checkout"]["enabled"] = False
    flags["checkout-v2"]["prerequisites"] = [{"flag_key": "new-checkout", "variation": "off"}]
    r = evaluate(flags["checkout-v2"], Context("u"), {}, flags)
    assert r.reason.kind == "PREREQUISITE_FAILED"


def test_prerequisite_can_require_a_specific_multivariate_variation():
    flags = {
        "theme": flag(
            key="theme",
            kind="string",
            variations=[{"key": "dark", "value": "dark"}, {"key": "light", "value": "light"}],
            off_variation="light",
            targets=[{"variation": "dark", "keys": ["night-owl"]}],
            fallthrough={"variation": "light"},
        ),
        "dark-charts": flag(
            key="dark-charts",
            prerequisites=[{"flag_key": "theme", "variation": "dark"}],
            fallthrough={"variation": "on"},
        ),
    }
    assert evaluate(flags["dark-charts"], Context("night-owl"), {}, flags).value is True
    assert evaluate(flags["dark-charts"], Context("early-bird"), {}, flags).value is False


def test_all_prerequisites_must_hold_and_first_failure_is_reported():
    flags = prereq_flags()
    flags["bundle"] = flag(
        key="bundle",
        prerequisites=[
            {"flag_key": "new-checkout", "variation": "on"},
            {"flag_key": "checkout-v2", "variation": "off"},
        ],
        fallthrough={"variation": "on"},
    )
    r = evaluate(flags["bundle"], Context("u", {"country": "BD"}), {}, flags)
    assert (r.reason.kind, r.reason.prerequisite_key) == ("PREREQUISITE_FAILED", "checkout-v2")
    r = evaluate(flags["bundle"], Context("u", {"country": "IN"}), {}, flags)
    assert r.reason.prerequisite_key == "new-checkout"


def test_kill_switch_beats_prerequisites_and_prerequisites_beat_targets():
    flags = prereq_flags()
    flags["checkout-v2"]["targets"] = [{"variation": "on", "keys": ["staff"]}]
    r = evaluate(flags["checkout-v2"], Context("staff"), {}, flags)
    assert r.reason.kind == "PREREQUISITE_FAILED"
    flags["checkout-v2"]["enabled"] = False
    assert evaluate(flags["checkout-v2"], Context("staff"), {}, flags).reason.kind == "OFF"


def test_missing_or_broken_prerequisite_is_not_met():
    flags = prereq_flags()
    del flags["new-checkout"]
    r = evaluate(flags["checkout-v2"], Context("u"), {}, flags)
    assert (r.reason.kind, r.reason.prerequisite_key) == ("PREREQUISITE_FAILED", "new-checkout")
    # without the flag map at all, nothing can be met
    assert evaluate(prereq_flags()["checkout-v2"], Context("u")).reason.kind == (
        "PREREQUISITE_FAILED"
    )
    flags = prereq_flags()
    flags["new-checkout"]["fallthrough"] = {"variation": "gone"}
    flags["checkout-v2"]["prerequisites"] = [{"flag_key": "new-checkout", "variation": "off"}]
    r = evaluate(flags["checkout-v2"], Context("u", {"country": "IN"}), {}, flags)
    assert r.reason.kind == "PREREQUISITE_FAILED"  # the prerequisite itself hit ERROR


def test_prerequisite_cycle_is_an_error_on_the_requested_flag():
    flags = prereq_flags()
    flags["new-checkout"]["prerequisites"] = [{"flag_key": "express-pay", "variation": "on"}]
    r = evaluate(flags["checkout-v2"], Context("u", {"country": "BD"}), {}, flags)
    assert (r.variation, r.reason.kind) == ("off", "ERROR")
    assert "checkout-v2 -> new-checkout -> express-pay -> checkout-v2" in r.reason.error
    itself = flag(prerequisites=[{"flag_key": "new-checkout", "variation": "on"}])
    r = evaluate(itself, Context("u"), {}, {"new-checkout": itself})
    assert r.reason.kind == "ERROR" and "cycle" in r.reason.error


def test_prerequisite_depth_limit():
    def chain(length):
        flags = {"f0": flag(key="f0", fallthrough={"variation": "on"})}
        for i in range(1, length + 1):
            flags[f"f{i}"] = flag(
                key=f"f{i}",
                prerequisites=[{"flag_key": f"f{i - 1}", "variation": "on"}],
                fallthrough={"variation": "on"},
            )
        return flags

    ok = chain(MAX_PREREQUISITE_DEPTH)
    r = evaluate(ok[f"f{MAX_PREREQUISITE_DEPTH}"], Context("u"), {}, ok)
    assert (r.value, r.reason.kind) == (True, "FALLTHROUGH")
    deep = chain(MAX_PREREQUISITE_DEPTH + 1)
    r = evaluate(deep[f"f{MAX_PREREQUISITE_DEPTH + 1}"], Context("u"), {}, deep)
    assert r.reason.kind == "ERROR" and "deeper than" in r.reason.error


def test_evaluate_all_resolves_prerequisites_from_the_same_map():
    results = evaluate_all(prereq_flags(), Context("u", {"country": "IN"}))
    assert {k: r.reason.kind for k, r in results.items()} == {
        "new-checkout": "FALLTHROUGH",
        "checkout-v2": "PREREQUISITE_FAILED",
        "express-pay": "PREREQUISITE_FAILED",
    }


# --- experiments with holdouts ----------------------------------------------------------------


def experiment_flag(holdout=10_000, experiment=None, **overrides):
    rollout = {
        "rollout": [
            {"variation": "on", "weight": 50_000},
            {"variation": "off", "weight": 50_000},
        ],
        "holdout": holdout,
    }
    if experiment:
        rollout["experiment"] = experiment
    return flag(
        kind="string",
        variations=[
            {"key": "control", "value": "classic"},
            {"key": "on", "value": "one-page"},
            {"key": "off", "value": "two-step"},
        ],
        off_variation="control",
        fallthrough=rollout,
        **overrides,
    )


def test_holdout_serves_off_variation_with_reason():
    f = experiment_flag(experiment="q4-checkout")
    results = [evaluate(f, Context(f"user-{i}")) for i in range(5_000)]
    held = [r for r in results if r.reason.in_holdout]
    assert abs(len(held) / len(results) - 0.10) < 0.02
    assert {r.variation for r in held} == {"control"}
    assert held[0].as_dict()["reason"] == {
        "kind": "FALLTHROUGH",
        "in_holdout": True,
        "experiment": "q4-checkout",
    }
    rest = [r for r in results if not r.reason.in_holdout]
    assert {r.variation for r in rest} == {"on", "off"}
    assert all(r.reason.in_rollout for r in rest)


def test_users_outside_the_holdout_keep_their_variation():
    without = experiment_flag(holdout=0)
    with_holdout = experiment_flag(holdout=20_000)
    for i in range(2_000):
        ctx = Context(f"user-{i}")
        r = evaluate(with_holdout, ctx)
        if not r.reason.in_holdout:
            assert r.variation == evaluate(without, ctx).variation


def test_holdout_membership_survives_weight_changes():
    f = experiment_flag()
    reweighted = experiment_flag()
    reweighted["fallthrough"]["rollout"] = [
        {"variation": "on", "weight": 90_000},
        {"variation": "off", "weight": 10_000},
    ]
    for i in range(2_000):
        ctx = Context(f"user-{i}")
        assert evaluate(f, ctx).reason.in_holdout == evaluate(reweighted, ctx).reason.in_holdout


def test_flags_sharing_an_experiment_hold_out_the_same_users():
    a = experiment_flag(experiment="q4-checkout")
    b = experiment_flag(experiment="q4-checkout", key="checkout-copy", salt="xyz")
    c = experiment_flag(key="checkout-copy", salt="xyz")
    same = different = 0
    for i in range(2_000):
        ctx = Context(f"user-{i}")
        held_a = evaluate(a, ctx).reason.in_holdout
        assert held_a == evaluate(b, ctx).reason.in_holdout
        same += held_a
        different += held_a and evaluate(c, ctx).reason.in_holdout
    assert same > 100 and different < same / 2


def test_holdout_on_a_rule_rollout_keeps_rule_reason():
    f = experiment_flag(holdout=99_999)
    f["rules"] = [{**bd_rule(), "variation": None, **f["fallthrough"]}]
    r = evaluate(f, Context("u", {"country": "BD"}))
    assert r.variation == "control"
    assert r.as_dict()["reason"] == {
        "kind": "RULE_MATCH",
        "rule_id": "bd",
        "rule_index": 0,
        "in_holdout": True,
    }


def test_malformed_holdout_is_an_error():
    f = experiment_flag(holdout="lots")
    r = evaluate(f, Context("u"))
    assert (r.variation, r.reason.kind) == ("control", "ERROR")
