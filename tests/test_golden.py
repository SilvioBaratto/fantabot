"""Byte-for-byte output of the asta commands, pinned before the simplification starts.

This is the gate the whole phase rests on. Deletions, renames, package moves, a rewritten
optimizer inner loop and four schema migrations all have to leave these bytes alone.

**Regeneration is deliberate on purpose.** `FANTABOT_GOLDEN_UPDATE=1` rewrites the expected
files *and then fails*, so a regeneration can never be the thing that makes a run green — it
has to be committed, reviewed as a per-file hash change in `MANIFEST.sha256`, and defended.
The failure mode this guards against is the ordinary one: a golden goes red for a reason
nobody wants to chase, somebody regenerates it, and the phase's only safety property quietly
becomes a rubber stamp.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path

import pytest
from _golden import GOLDEN, PINNED_TODAY, run

UPDATE = os.environ.get("FANTABOT_GOLDEN_UPDATE") == "1"

EXPECTED = GOLDEN / "expected"
MANIFEST = GOLDEN / "MANIFEST.sha256"

#: Each case exercises a path the phase is about to disturb. `no_sentiment` is the ablation
#: control CLAUDE.md calls load-bearing — it must reproduce the pre-sentiment value model
#: field for field, so a change to the sentiment layer that leaks into it shows up here.
CASES: dict[str, list[str]] = {
    "optimize_default": ["asta-optimize", "--budget", "500", "--lam", "0.3"],
    "optimize_no_sentiment": ["asta-optimize", "--budget", "500", "--lam", "0.3", "--no-sentiment"],
    "optimize_lam_zero": ["asta-optimize", "--budget", "500", "--lam", "0"],
    "optimize_tilt_zero": ["asta-optimize", "--budget", "500", "--lam", "0.3", "--tilt-k", "0"],
    "legality_no_xi": ["asta-legality", "--rosa", "2170,6675,4227"],
    "live_replay_unowned": [
        "asta-live",
        "--replay",
        "tests/fixtures/states/one_auction.jsonl",
        "--team",
        "t1",
        "--lam",
        "0.3",
    ],
    # Pins a real defect, not a success: every id in the replay is a FantaLab UUID and the
    # pool is keyed by fantacalcio id, so the moment our team owns anything `optimize_roster`
    # raises. `--team t1` passes only because t1 never buys. Captured so that fixing the
    # mapping is a deliberate, reviewable golden change.
    "live_replay_owned_raises": [
        "asta-live",
        "--replay",
        "tests/fixtures/states/one_auction.jsonl",
        "--team",
        "a1bdb260-59f3-4221-af85-572ce57e1f1e",
        "--lam",
        "0.3",
    ],
}

#: Cases run at a later `as_of`, so the 7-day confidence decay is exercised. Without one,
#: `half_life_days` is structurally invisible: every stored row shares one `data_run`, so at
#: zero age the decay is 1.0 for any half-life and a mutation to it cannot be caught.
AGED_CASES: dict[str, tuple[list[str], int]] = {
    "optimize_aged_7d": (["asta-optimize", "--budget", "500", "--lam", "0.3"], 7),
}


def _expected_path(name: str) -> Path:
    return EXPECTED / f"{name}.txt"


@pytest.mark.parametrize("name", sorted(CASES))
def test_output_is_byte_identical(name: str) -> None:
    actual = run(CASES[name])
    path = _expected_path(name)

    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return

    assert path.exists(), f"{path} is missing — run with FANTABOT_GOLDEN_UPDATE=1 to create it"
    assert actual == path.read_text(encoding="utf-8"), (
        f"{name} changed. If this is a regression, fix the code. If it is a deliberate "
        "behaviour change, regenerate with FANTABOT_GOLDEN_UPDATE=1, commit the new bytes "
        "and the new MANIFEST.sha256, and say why in the commit message."
    )


@pytest.mark.parametrize("name", sorted(AGED_CASES))
def test_aged_output_is_byte_identical(name: str) -> None:
    argv, days = AGED_CASES[name]
    actual = run(argv, today=PINNED_TODAY + timedelta(days=days))
    path = _expected_path(name)

    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return

    assert path.exists(), f"{path} is missing — run with FANTABOT_GOLDEN_UPDATE=1 to create it"
    assert actual == path.read_text(encoding="utf-8"), f"{name} changed"


def test_the_fixture_files_match_their_manifest() -> None:
    """The inputs are pinned too — a golden over drifting inputs proves nothing."""
    if UPDATE:
        lines = [
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(GOLDEN)}"
            for p in sorted(GOLDEN.rglob("*"))
            if p.is_file() and p.name != MANIFEST.name
        ]
        MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    assert MANIFEST.exists(), "tests/golden/MANIFEST.sha256 is missing"
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, relative = line.partition("  ")
        path = GOLDEN / relative
        assert path.exists(), f"{relative} is in the manifest but not on disk"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == digest, f"{relative} changed without its manifest entry"


def test_update_mode_cannot_be_the_thing_that_makes_a_run_green() -> None:
    """A regeneration must be committed and defended, never used to clear a red run.

    Without this, the cheapest response to a failing golden is `FANTABOT_GOLDEN_UPDATE=1`
    and a green suite, which converts the phase's one safety property into a rubber stamp.
    """
    assert not UPDATE, (
        "FANTABOT_GOLDEN_UPDATE=1 rewrote the expected files and this failure is deliberate. "
        "Review `git diff tests/golden/`, commit it with a reason, then re-run without the "
        "variable set."
    )
