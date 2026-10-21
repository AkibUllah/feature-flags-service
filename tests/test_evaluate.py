from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import evaluate, evaluate_all, segment_matches


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
