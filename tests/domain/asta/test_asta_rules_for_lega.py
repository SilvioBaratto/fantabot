"""The roster band comes from the lega's own snapshot — on both surfaces, in both formats.

`asta optimize` and `asta bid` built a bare `RosterRules()` — **size 30, whatever the lega
declares** — while `GET /asta/plan` read the snapshotted band. That is not a cosmetic
difference: on 2026-08-26 `settings/rosters` read 30/30 with `minrl = maxrl = [2, 28]`; on
2026-09-02 it read **25/32 with `minrl=[2, 23]`, `maxrl=[4, 28]`** — a variable roster size
and a real per-role band where there had been none. A plan built on 30 against a 25-man lega
cannot be bought, and `1.14` found exactly that: the real command exits 1 with *"cannot
complete the roster: 19/30 filled"*.

**Provenance is a third named constant, not prose.** `rules_for_room` returns
`ROOM_DECLARED` / `ASSUMED_NOTHING` and its docstring is the argument: *"a provenance an
operator cannot grep for consistently is one they stop trusting"*. A band read from a
snapshot is neither of those two — it is what the *lega* declares at its last sync, not what
a *room* declares tonight, and the two can disagree. So `SNAPSHOT_DECLARED`.

Pure: this takes the four primitives off a snapshot, never the ORM row, so `domain/` keeps
knowing nothing about persistence.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.state import (
    ASSUMED_NOTHING,
    SNAPSHOT_DECLARED,
    RosterRules,
    rules_for_lega,
)
from fantabot.domain.classic.state import ClassicRosterRules

#: The lega as it read on 2026-09-02 — the capture that made this task necessary.
MANTRA_2026_09_02 = {
    "role_groups": 2,
    "roster_size": 25,
    "min_roles": [2, 23],
    "max_roles": [4, 28],
}
#: Lega 3584692 (Legamiallerotaie), Classic: `sroles=1`, `minrl=[3, 8, 8, 6]`, 25-man.
CLASSIC_LEGA = {
    "role_groups": 1,
    "roster_size": 25,
    "min_roles": [3, 8, 8, 6],
    "max_roles": [3, 8, 8, 6],
}


class TestMantra:
    def test_the_band_is_the_lega_s_own(self) -> None:
        rules, provenance = rules_for_lega(**MANTRA_2026_09_02)  # type: ignore[arg-type]

        assert isinstance(rules, RosterRules)
        assert rules.size == 25
        assert rules.min_goalkeepers == 2
        assert rules.min_movement == 23
        assert provenance == SNAPSHOT_DECLARED

    def test_it_is_not_the_hardcoded_thirty(self) -> None:
        """The whole point. `RosterRules()` is 30/2/28 and was used regardless of the lega."""
        rules, _ = rules_for_lega(**MANTRA_2026_09_02)  # type: ignore[arg-type]

        assert (rules.size, rules.min_goalkeepers, rules.min_movement) != (
            RosterRules().size,
            RosterRules().min_goalkeepers,
            RosterRules().min_movement,
        )


class TestClassic:
    def test_the_four_bands_come_from_min_roles(self) -> None:
        """The app's version was Mantra-only and fell back to a bare `ClassicRosterRules()`
        even when the snapshot carried the band."""
        rules, provenance = rules_for_lega(**CLASSIC_LEGA)  # type: ignore[arg-type]

        assert isinstance(rules, ClassicRosterRules)
        assert rules.size == 25
        assert rules.bands == (("P", 3, 3), ("D", 8, 8), ("C", 8, 8), ("A", 6, 6))
        assert provenance == SNAPSHOT_DECLARED

    def test_max_roles_widens_the_band_when_the_lega_declares_one(self) -> None:
        """`minrl` and `maxrl` are separate settings and 2026-09-02 proved they can differ."""
        rules, _ = rules_for_lega(
            role_groups=1, roster_size=26, min_roles=[3, 8, 8, 6], max_roles=[3, 9, 9, 7]
        )

        assert rules.bands == (("P", 3, 3), ("D", 8, 9), ("C", 8, 9), ("A", 6, 7))


class TestFallingBack:
    """Every incomplete snapshot lands on the default **and says so**. A band nobody
    declared and a band the lega stated are different facts, and only one is worth
    planning on."""

    @pytest.mark.parametrize(
        "snapshot",
        [
            {"role_groups": 2, "roster_size": None, "min_roles": [2, 23], "max_roles": None},
            {"role_groups": 2, "roster_size": 25, "min_roles": None, "max_roles": None},
            {"role_groups": 2, "roster_size": 25, "min_roles": [2], "max_roles": None},
            {"role_groups": None, "roster_size": None, "min_roles": None, "max_roles": None},
        ],
        ids=["no size", "no roles", "too few roles", "nothing at all"],
    )
    def test_an_incomplete_snapshot_is_assumed_not_invented(self, snapshot: dict) -> None:
        rules, provenance = rules_for_lega(**snapshot)

        assert provenance == ASSUMED_NOTHING
        assert rules == RosterRules()

    def test_a_classic_lega_with_no_band_falls_back_to_classic_and_not_to_mantra(self) -> None:
        """`role_groups` is known even when the band is not, and it decides the *type*.
        Falling back to `RosterRules()` here would plan a Classic lega against 11 schemi."""
        rules, provenance = rules_for_lega(
            role_groups=1, roster_size=None, min_roles=None, max_roles=None
        )

        assert isinstance(rules, ClassicRosterRules)
        assert rules == ClassicRosterRules()
        assert provenance == ASSUMED_NOTHING


def test_the_three_provenances_are_distinct_and_greppable() -> None:
    """`rules_for_room`'s rule, extended. Three sources, three exact strings — a reader who
    greps one must not find another, and none is composed at a call site."""
    from fantabot.domain.asta.state import ASSUMED_NOTHING, ROOM_DECLARED, SNAPSHOT_DECLARED

    all_three = {ROOM_DECLARED, ASSUMED_NOTHING, SNAPSHOT_DECLARED}

    assert len(all_three) == 3
    assert not any(a in b for a in all_three for b in all_three if a != b)
