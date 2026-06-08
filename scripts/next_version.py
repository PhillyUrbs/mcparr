#!/usr/bin/env python3
"""Compute the next release version for mcparr's beta/stable channels.

Pure, dependency-free version math so it can run in CI and be unit-tested.

Channels and rules (with ``--bump auto``):

- beta:   patch-bump the latest stable, then start or continue a ``-beta.N``
          series for that base. e.g. latest stable ``v0.1.0`` ->
          ``v0.1.1-beta.1`` -> ``v0.1.1-beta.2`` -> ...
- stable: if a beta series is in flight ahead of the latest stable, promote it
          (drop the ``-beta.N`` suffix). Otherwise patch-bump the latest stable.

``--bump patch|minor|major`` overrides the base bump applied to the latest
stable for either channel.

Usage:
    python scripts/next_version.py --channel beta --bump auto
    python scripts/next_version.py --channel stable --bump minor

Tags are read from ``git tag`` unless ``--tags`` is given (whitespace/comma
separated). Output is a single version string such as ``v0.1.1-beta.1``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Iterable
from typing import NamedTuple

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")

Base = tuple[int, int, int]


class Version(NamedTuple):
    major: int
    minor: int
    patch: int
    beta: int | None  # None => stable release

    @property
    def base(self) -> Base:
        return (self.major, self.minor, self.patch)

    def format(self) -> str:
        core = f"v{self.major}.{self.minor}.{self.patch}"
        return f"{core}-beta.{self.beta}" if self.beta is not None else core


def parse(tag: str) -> Version | None:
    """Parse a single tag, returning ``None`` for anything non-conforming."""
    match = _TAG_RE.match(tag.strip())
    if match is None:
        return None
    beta = int(match[4]) if match[4] is not None else None
    return Version(int(match[1]), int(match[2]), int(match[3]), beta)


def parse_tags(tags: Iterable[str]) -> list[Version]:
    return [v for v in (parse(t) for t in tags) if v is not None]


def latest_stable(versions: list[Version]) -> Version:
    stables = [v for v in versions if v.beta is None]
    if not stables:
        return Version(0, 0, 0, None)
    return max(stables, key=lambda v: v.base)


def _bumped_base(stable: Version, bump: str) -> Base:
    if bump in ("auto", "patch"):
        return (stable.major, stable.minor, stable.patch + 1)
    if bump == "minor":
        return (stable.major, stable.minor + 1, 0)
    if bump == "major":
        return (stable.major + 1, 0, 0)
    raise ValueError(f"unknown bump: {bump!r}")


def next_version(tags: Iterable[str], channel: str, bump: str = "auto") -> str:
    """Return the next version tag string for the given channel and bump."""
    versions = parse_tags(tags)
    stable = latest_stable(versions)

    if channel == "beta":
        base = _bumped_base(stable, bump)
        existing = [v.beta for v in versions if v.beta is not None and v.base == base]
        number = (max(existing) + 1) if existing else 1
        return Version(*base, number).format()

    if channel == "stable":
        if bump == "auto":
            ahead = [v.base for v in versions if v.beta is not None and v.base > stable.base]
            if ahead:
                return Version(*max(ahead), None).format()
            return Version(*_bumped_base(stable, "patch"), None).format()
        return Version(*_bumped_base(stable, bump), None).format()

    raise ValueError(f"unknown channel: {channel!r}")


def _git_tags() -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute the next mcparr release version.")
    parser.add_argument("--channel", required=True, choices=["beta", "stable"])
    parser.add_argument("--bump", default="auto", choices=["auto", "patch", "minor", "major"])
    parser.add_argument(
        "--tags",
        default=None,
        help="Override the tag list (whitespace/comma separated). Defaults to `git tag`.",
    )
    args = parser.parse_args(argv)

    if args.tags is not None:
        tags = [t for t in re.split(r"[\s,]+", args.tags.strip()) if t]
    else:
        tags = _git_tags()

    print(next_version(tags, args.channel, args.bump))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
