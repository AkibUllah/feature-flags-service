import pytest

from feature_flags.core.clauses import Context, clause_matches

ctx = Context(
    "user_42",
    {
        "country": "BD",
        "plan": "pro",
        "age": 31,
        "app_version": "2.4.1",
        "roles": ["admin", "ops"],
        "email": "akib@dhakagrocers.example",
    },
)


def clause(attribute, op, *values, negate=False):
    return {"attribute": attribute, "op": op, "values": list(values), "negate": negate}


@pytest.mark.parametrize(
    ("c", "expected"),
    [
        (clause("country", "eq", "BD"), True),
        (clause("country", "eq", "IN"), False),
        (clause("country", "neq", "IN"), True),
        (clause("plan", "in", "free", "pro"), True),
        (clause("plan", "not_in", "free", "pro"), False),
        (clause("email", "contains", "dhaka"), True),
        (clause("email", "starts_with", "akib@"), True),
        (clause("email", "ends_with", ".example"), True),
        (clause("email", "regex", r"^[a-z]+@dhaka.*\.example$"), True),
        (clause("email", "regex", "("), False),  # invalid pattern never matches
        (clause("age", "gt", 30), True),
        (clause("age", "gte", 31), True),
        (clause("age", "lt", 31), False),
        (clause("age", "lte", "31"), True),  # numeric strings compare numerically
        (clause("app_version", "semver_gte", "2.4.0"), True),
        (clause("app_version", "semver_gt", "2.4.1"), False),
        (clause("app_version", "semver_lt", "3.0.0-beta"), True),
        (clause("app_version", "semver_eq", "2.4.1"), True),
        (clause("roles", "eq", "ops"), True),  # list attribute: any element
        (clause("roles", "in", "viewer"), False),
        (clause("missing", "exists"), False),
        (clause("country", "exists"), True),
        (clause("key", "in", "user_42"), True),
        (clause("missing", "eq", "x"), False),
        (clause("missing", "neq", "x"), True),
    ],
)
def test_operators(c, expected):
    assert clause_matches(c, ctx) is expected


def test_negate_inverts():
    assert clause_matches(clause("country", "eq", "BD", negate=True), ctx) is False
    assert clause_matches(clause("missing", "exists", negate=True), ctx) is True


def test_non_string_values_for_string_ops_do_not_match():
    assert clause_matches(clause("age", "contains", "3"), ctx) is False
    assert clause_matches(clause("plan", "gt", 1), ctx) is False
    assert clause_matches(clause("plan", "semver_gte", "1.0.0"), ctx) is False


def test_unknown_operator_raises():
    with pytest.raises(ValueError):
        clause_matches(clause("country", "like", "B%"), ctx)
    with pytest.raises(ValueError):
        clause_matches(clause("country", "in_segment", "beta"), ctx)
