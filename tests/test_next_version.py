"""Tests for the release version calculator in ``scripts/next_version.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "next_version.py"
_spec = importlib.util.spec_from_file_location("next_version", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
next_version_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(next_version_module)

next_version = next_version_module.next_version


BASELINE = ["v0.1.0"]


@pytest.mark.parametrize(
    ("tags", "channel", "bump", "expected"),
    [
        # --- beta, auto (patch base off latest stable) ---
        (BASELINE, "beta", "auto", "v0.1.1-beta.1"),
        (BASELINE + ["v0.1.1-beta.1"], "beta", "auto", "v0.1.1-beta.2"),
        (BASELINE + ["v0.1.1-beta.1", "v0.1.1-beta.2"], "beta", "auto", "v0.1.1-beta.3"),
        # no tags at all -> first beta off 0.0.0
        ([], "beta", "auto", "v0.0.1-beta.1"),
        # --- beta, explicit base overrides ---
        (BASELINE, "beta", "patch", "v0.1.1-beta.1"),
        (BASELINE, "beta", "minor", "v0.2.0-beta.1"),
        (BASELINE, "beta", "major", "v1.0.0-beta.1"),
        # continue a minor-based beta series
        (BASELINE + ["v0.2.0-beta.1"], "beta", "minor", "v0.2.0-beta.2"),
        # switching base starts a fresh series at .1
        (BASELINE + ["v0.1.1-beta.1"], "beta", "minor", "v0.2.0-beta.1"),
        # --- stable, auto: promote in-flight beta ---
        (BASELINE + ["v0.1.1-beta.1"], "stable", "auto", "v0.1.1"),
        (BASELINE + ["v0.1.1-beta.1", "v0.1.1-beta.2"], "stable", "auto", "v0.1.1"),
        # promote the highest beta base when several are ahead
        (BASELINE + ["v0.1.1-beta.1", "v0.2.0-beta.1"], "stable", "auto", "v0.2.0"),
        # --- stable, auto: no beta in flight -> patch bump ---
        (BASELINE, "stable", "auto", "v0.1.1"),
        (["v0.1.0", "v0.1.1"], "stable", "auto", "v0.1.2"),
        # --- stable, explicit bump overrides ---
        (BASELINE, "stable", "patch", "v0.1.1"),
        (BASELINE, "stable", "minor", "v0.2.0"),
        (BASELINE, "stable", "major", "v1.0.0"),
        # explicit bump ignores in-flight betas
        (BASELINE + ["v0.1.1-beta.1"], "stable", "minor", "v0.2.0"),
        # --- latest stable is picked across several stable tags ---
        (["v0.1.0", "v0.2.0", "v0.1.5"], "beta", "auto", "v0.2.1-beta.1"),
        # non-conforming tags are ignored
        (["v0.1.0", "nightly", "latest", "v0.1.1-beta.1"], "stable", "auto", "v0.1.1"),
    ],
)
def test_next_version(tags: list[str], channel: str, bump: str, expected: str) -> None:
    assert next_version(tags, channel, bump) == expected


def test_unknown_channel() -> None:
    with pytest.raises(ValueError, match="unknown channel"):
        next_version(BASELINE, "nightly")


def test_unknown_bump() -> None:
    with pytest.raises(ValueError, match="unknown bump"):
        next_version(BASELINE, "stable", "huge")
