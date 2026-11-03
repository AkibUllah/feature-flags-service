"""Saving prerequisites and experiment holdouts: validation, payloads and archive guards."""

from datetime import timedelta

import pytest

from feature_flags.core.clauses import Context
from feature_flags.core.evaluate import MAX_PREREQUISITE_DEPTH
from feature_flags.services import audit, evaluation, flags, payloads, projects, scheduled
from feature_flags.services.errors import Conflict, DomainError
from tests.conftest import BOOL_VARIATIONS, T0, enable

THEME_VARIATIONS = [{"key": "dark", "value": "dark"}, {"key": "light", "value": "light"}]


def make_flag(session, ctx, project, key, variations=BOOL_VARIATIONS, kind="boolean"):
    return flags.create_flag(
        session,
        ctx,
        project,
        key=key,
        name=key,
        kind=kind,
        variations=variations,
        now=T0,
        actor="akib",
    )


def requires(*pairs):
    return [{"flag_key": key, "variation": variation} for key, variation in pairs]


def patch(session, ctx, project, flag, env, **changes):
    return flags.update_config(session, ctx, project, flag, env, changes, now=T0, actor="akib")


@pytest.fixture
def chain(session, ctx, project, bool_flag):
    """new-checkout <- checkout-v2 (needs new-checkout == on)."""
    v2 = make_flag(session, ctx, project, "checkout-v2")
    return bool_flag, v2


def test_prerequisites_are_saved_audited_and_published(session, ctx, project, prod, chain, kv):
    base, v2 = chain
    enable(
        session,
        ctx,
        project,
        base,
        prod,
        targets=[{"variation": "on", "keys": ["staff"]}],
        fallthrough={"variation": "off"},
    )
    config = enable(session, ctx, project, v2, prod, prerequisites=requires(("new-checkout", "on")))
    assert config.prerequisites == requires(("new-checkout", "on"))
    entry = audit.list_entries(session, project.id, entity_type="flag", entity_id="checkout-v2")[0]
    assert entry.after["prerequisites"] == requires(("new-checkout", "on"))

    payload, _ = payloads.cached_payload(session, kv, prod, 60)
    assert payload["flags"]["checkout-v2"]["prerequisites"] == requires(("new-checkout", "on"))
    assert payload["flags"]["new-checkout"]["prerequisites"] == []

    staff, guest = Context("staff"), Context("guest")
    [hit] = evaluation.evaluate_flags(session, ctx, prod, staff, ["checkout-v2"], T0)
    [miss] = evaluation.evaluate_flags(session, ctx, prod, guest, ["checkout-v2"], T0)
    assert (hit.value, hit.reason.kind) == (True, "FALLTHROUGH")
    assert (miss.value, miss.reason.as_dict()) == (
        False,
        {"kind": "PREREQUISITE_FAILED", "prerequisite_key": "new-checkout"},
    )
    view = payloads.client_payload(payload, guest)
    assert view["flags"]["checkout-v2"]["reason"]["kind"] == "PREREQUISITE_FAILED"


def test_prerequisite_validation_errors(session, ctx, project, prod, chain):
    base, v2 = chain
    archived = make_flag(session, ctx, project, "old-checkout")
    flags.archive_flag(session, ctx, project, archived, now=T0, actor="akib")
    cases = [
        ("not-a-list", "must be a list"),
        (["new-checkout"], "'flag_key' and 'variation'"),
        (requires(("ghost", "on")), "unknown prerequisite flag 'ghost'"),
        (requires(("old-checkout", "on")), "unknown prerequisite flag 'old-checkout'"),
        (requires(("new-checkout", "maybe")), "has no variation 'maybe'"),
        (requires(("checkout-v2", "on")), "cannot be its own prerequisite"),
        (requires(("new-checkout", "on"), ("new-checkout", "off")), "listed twice"),
    ]
    for prerequisites, message in cases:
        with pytest.raises(DomainError, match=message):
            patch(session, ctx, project, v2, prod, prerequisites=prerequisites)
    assert flags.get_config(v2, prod).prerequisites == []


def test_prerequisite_cycles_are_rejected_with_the_path(session, ctx, project, prod, chain):
    base, v2 = chain
    pay = make_flag(session, ctx, project, "express-pay")
    patch(session, ctx, project, v2, prod, prerequisites=requires(("new-checkout", "on")))
    patch(session, ctx, project, pay, prod, prerequisites=requires(("checkout-v2", "on")))
    with pytest.raises(DomainError, match="checkout-v2") as two:
        patch(session, ctx, project, v2, prod, prerequisites=requires(("express-pay", "on")))
    assert two.value.code == "prerequisite_cycle"
    with pytest.raises(DomainError) as three:
        patch(session, ctx, project, base, prod, prerequisites=requires(("express-pay", "off")))
    assert three.value.code == "prerequisite_cycle"
    assert "new-checkout -> express-pay -> checkout-v2 -> new-checkout" in three.value.message
    assert flags.get_config(base, prod).prerequisites == []


def test_prerequisite_graphs_are_per_environment(session, ctx, project, prod, chain):
    base, v2 = chain
    staging = projects.get_environment(session, project, "staging")
    patch(session, ctx, project, v2, prod, prerequisites=requires(("new-checkout", "on")))
    # the opposite edge in another environment is not a cycle
    config = patch(
        session, ctx, project, base, staging, prerequisites=requires(("checkout-v2", "on"))
    )
    assert config.prerequisites == requires(("checkout-v2", "on"))


def test_prerequisite_depth_counts_dependents_too(session, ctx, project, prod):
    chain = [
        make_flag(session, ctx, project, f"step-{i}") for i in range(MAX_PREREQUISITE_DEPTH + 2)
    ]
    # step-1 -> step-0, ..., step-MAX -> step-(MAX-1): exactly MAX levels
    for i in range(1, MAX_PREREQUISITE_DEPTH + 1):
        patch(
            session, ctx, project, chain[i], prod, prerequisites=requires((f"step-{i - 1}", "on"))
        )
    top = chain[MAX_PREREQUISITE_DEPTH + 1]
    with pytest.raises(DomainError, match="levels deep") as too_deep:
        patch(
            session,
            ctx,
            project,
            top,
            prod,
            prerequisites=requires((f"step-{MAX_PREREQUISITE_DEPTH}", "on")),
        )
    assert too_deep.value.code == "prerequisite_too_deep"
    # lengthening the chain from the bottom is caught as well
    with pytest.raises(DomainError, match="levels deep"):
        patch(session, ctx, project, chain[0], prod, prerequisites=requires((top.key, "on")))


def test_prerequisite_of_a_multivariate_flag(session, ctx, project, prod, chain):
    _, v2 = chain
    make_flag(session, ctx, project, "theme", THEME_VARIATIONS, kind="string")
    config = patch(session, ctx, project, v2, prod, prerequisites=requires(("theme", "dark")))
    assert config.prerequisites == requires(("theme", "dark"))
    with pytest.raises(DomainError, match="has no variation 'on'"):
        patch(session, ctx, project, v2, prod, prerequisites=requires(("theme", "on")))


def test_archiving_a_prerequisite_is_refused_while_used(session, ctx, project, prod, chain):
    base, v2 = chain
    staging = projects.get_environment(session, project, "staging")
    patch(session, ctx, project, v2, staging, prerequisites=requires(("new-checkout", "on")))
    with pytest.raises(Conflict, match="checkout-v2") as refused:
        flags.archive_flag(session, ctx, project, base, now=T0, actor="akib")
    assert refused.value.code == "flag_in_use" and base.archived is False
    patch(session, ctx, project, v2, staging, prerequisites=[])
    assert flags.archive_flag(session, ctx, project, base, now=T0, actor="akib").archived


def test_archived_dependents_do_not_block_archiving(session, ctx, project, prod, chain):
    base, v2 = chain
    patch(session, ctx, project, v2, prod, prerequisites=requires(("new-checkout", "on")))
    flags.archive_flag(session, ctx, project, v2, now=T0, actor="akib")
    assert flags.archive_flag(session, ctx, project, base, now=T0, actor="akib").archived


def test_scheduled_prerequisite_change_is_validated_up_front(session, ctx, project, prod, chain):
    base, v2 = chain
    patch(session, ctx, project, v2, prod, prerequisites=requires(("new-checkout", "on")))
    with pytest.raises(DomainError, match="prerequisite cycle"):
        scheduled.schedule(
            session,
            project,
            base,
            prod,
            scheduled_at=T0 + timedelta(hours=1),
            changes={"prerequisites": requires(("checkout-v2", "on"))},
            now=T0,
            actor="akib",
        )
    change = scheduled.schedule(
        session,
        project,
        v2,
        prod,
        scheduled_at=T0 + timedelta(hours=1),
        changes={"prerequisites": []},
        now=T0,
        actor="akib",
    )
    scheduled.apply_due(session, ctx, T0 + timedelta(hours=1))
    assert change.status == "applied" and flags.get_config(v2, prod).prerequisites == []


# --- holdouts ---------------------------------------------------------------------------------

SPLIT = [{"variation": "on", "weight": 50_000}, {"variation": "off", "weight": 50_000}]


def test_holdout_validation(session, ctx, project, bool_flag, prod):
    cases = [
        ({"variation": "on", "holdout": 5_000}, "only use 'holdout' or 'experiment'"),
        ({"variation": "on", "experiment": "q4"}, "only use 'holdout' or 'experiment'"),
        ({"rollout": SPLIT, "holdout": 100_000}, "holdout must be"),
        ({"rollout": SPLIT, "holdout": -1}, "holdout must be"),
        ({"rollout": SPLIT, "holdout": "10%"}, "integer weight"),
        ({"rollout": SPLIT, "holdout": 5_000, "experiment": "Q4 Checkout"}, "experiment key"),
    ]
    for fallthrough, message in cases:
        with pytest.raises(DomainError, match=message):
            patch(session, ctx, project, bool_flag, prod, fallthrough=fallthrough)
    rule = {
        "id": "bd",
        "clauses": [{"attribute": "country", "op": "eq", "values": ["BD"]}],
        "rollout": SPLIT,
        "holdout": 100_001,
    }
    with pytest.raises(DomainError, match="rule 'bd' rollout invalid"):
        patch(session, ctx, project, bool_flag, prod, rules=[rule])


def test_holdout_is_saved_and_served(session, ctx, project, bool_flag, prod):
    fallthrough = {"rollout": SPLIT, "holdout": 10_000, "experiment": "q4-checkout"}
    enable(session, ctx, project, bool_flag, prod, fallthrough=fallthrough)
    results = [
        evaluation.evaluate_flags(session, ctx, prod, Context(f"u{i}"), ["new-checkout"], T0)[0]
        for i in range(2_000)
    ]
    held = [r for r in results if r.reason.in_holdout]
    assert 0.07 < len(held) / len(results) < 0.13
    assert {r.variation for r in held} == {"off"}
    assert all(r.reason.experiment == "q4-checkout" for r in results)
