"""Semantic version parsing and comparison (semver.org 2.0.0, including pre-release ordering)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_SEMVER = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class InvalidVersion(ValueError):
    pass


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: str) -> Version:
        match = _SEMVER.match(raw.strip())
        if not match:
            raise InvalidVersion(f"not a semantic version: {raw!r}")
        pre = tuple(match["pre"].split(".")) if match["pre"] else ()
        return cls(int(match["major"]), int(match["minor"]), int(match["patch"] or 0), pre)

    def _key(self) -> tuple:
        return (self.major, self.minor, self.patch)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key() and self.prerelease == other.prerelease

    def __lt__(self, other: Version) -> bool:
        if self._key() != other._key():
            return self._key() < other._key()
        # A version without a pre-release tag is higher than one with it.
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for mine, theirs in zip(self.prerelease, other.prerelease, strict=False):
            if mine == theirs:
                continue
            mine_num, theirs_num = mine.isdigit(), theirs.isdigit()
            if mine_num and theirs_num:
                return int(mine) < int(theirs)
            if mine_num != theirs_num:
                return mine_num  # numeric identifiers sort before alphanumeric ones
            return mine < theirs
        return len(self.prerelease) < len(other.prerelease)

    def __hash__(self) -> int:
        return hash((self._key(), self.prerelease))

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{'.'.join(self.prerelease)}" if self.prerelease else base
