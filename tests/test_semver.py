import pytest

from feature_flags.core.semver import InvalidVersion, Version


def test_parse_variants():
    assert Version.parse("1.2.3") == Version(1, 2, 3)
    assert Version.parse("v2.0") == Version(2, 0, 0)
    assert Version.parse("1.0.0-beta.2+build.5") == Version(1, 0, 0, ("beta", "2"))
    assert str(Version.parse("1.0.0-rc.1+sha")) == "1.0.0-rc.1"


@pytest.mark.parametrize("raw", ["", "1", "1.2.3.4", "01.2.3", "1.2.3-", "a.b.c"])
def test_invalid_versions(raw):
    with pytest.raises(InvalidVersion):
        Version.parse(raw)


def test_ordering_follows_semver_spec():
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
        "1.0.1",
        "1.1.0",
        "2.0.0",
    ]
    parsed = [Version.parse(v) for v in ordered]
    assert parsed == sorted(parsed)
    for earlier, later in zip(parsed, parsed[1:], strict=False):
        assert earlier < later and later > earlier and earlier != later


def test_build_metadata_ignored_in_equality():
    assert Version.parse("1.2.3+a") == Version.parse("1.2.3+b")
    assert Version.parse("1.2.3") >= Version.parse("1.2.3-rc.1")
