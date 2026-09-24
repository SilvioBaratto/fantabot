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

**`maxrl` is the other half of that drift, and the Mantra path dropped it** until
2026-09-24. `RosterRules` had no field for a declared ceiling, so `max_goalkeepers()` fell
back to `size - min_movement` — **9** on the live `32 / [2, 23] / [4, 28]` against the 4 the
lega permits — and both readers of that method fail open. The case below asserted `[4, 28]`
in its fixture and nothing about it; that is the test that should have caught this.

**Reading it back was not the same as honouring it.** The first fix guarded the new ceilings
with `sum(maxrl) < roster_size -> drop both`, still labelled `SNAPSHOT_DECLARED`: on the live
lega `4 + 28 = 32 = xsltc`, so the margin was zero and the test was `<`, and one added to
`xsltc` reinstated the 9-keeper band under a provenance saying the lega had declared it.
`TestCeilingsThatCannotFillTheDeclaredSize` is that case. A shortfall now clamps the size the
ceilings can fill instead of throwing the ceilings away, and a `maxrl` that is short or
carries a 0 is read by the half it has.

**Three tests here were vacuous and are rewritten, not deleted.** Each asserted a value the
pre-fix code returned too — the derivation compared to itself, a 25-man band where declared
and derived coincide, and a `>=` that a ceiling shrink can only widen. Each now asserts the
number that *moves*.

**Four more mutants outlived that round and are pinned here (2026-09-24).** The refusal
message named `{size}` instead of the ceiling and `match=r"cannot be filled"` could not tell;
`_declared_ceiling`'s `> 0` weakened to `!= 0`, which turns a negative `maxrl` entry into a
ceiling clamped up to its own floor; both `max_*_declared` field defaults turned into `0`,
which this file never exercised because every path to "nothing declared" ran through
`_declared_ceiling`; and the Classic `highs` length test weakened to `>= 1`, whose fallback
had no test because every Classic snapshot here declares all four ceilings. Three of the four
survived all 82 tests across this file and its three siblings; the field defaults survived
this file's own 37 and were caught only by `test_asta_resize_band.py`, incidentally, through
a `resize_band` that started refusing.

Pure: this takes the four primitives off a snapshot, never the ORM row, so `domain/` keeps
knowing nothing about persistence.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.legality import SchemaLegality, SlotRule
from fantabot.domain.asta.optimizer import optimize_roster
from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import (
    ASSUMED_NOTHING,
    SNAPSHOT_DECLARED,
    AstaState,
    RosterRules,
    drop_unvaluable,
    resize_band,
    rules_for_lega,
)
from fantabot.domain.asta.value import NaiveValueModel
from fantabot.domain.classic.state import ClassicRosterRules

#: The lega as it read on 2026-09-02 — the capture that made this task necessary.
MANTRA_2026_09_02 = {
    "role_groups": 2,
    "roster_size": 25,
    "min_roles": [2, 23],
    "max_roles": [4, 28],
}
#: Lega 4103937 as the live database holds it: `msltc/xsltc = 25/32`, and the size is
#: `xsltc` (`domain/lega/parse.py`). The band `test_asta_resize_band`'s docstring quotes.
LIVE_4103937 = {"role_groups": 2, "roster_size": 32, "min_roles": [2, 23], "max_roles": [4, 28]}
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

    def test_both_halves_of_the_snapshot_are_read_and_not_just_minrl(self) -> None:
        """`max_roles=[4, 28]` sat in this fixture from the day it was written, asserted
        nowhere, while the Mantra return read `min_roles` and dropped it."""
        rules, _ = rules_for_lega(**MANTRA_2026_09_02)  # type: ignore[arg-type]

        assert isinstance(rules, RosterRules)
        assert (rules.max_goalkeepers_declared, rules.max_movement_declared) == (4, 28)

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

    @pytest.mark.parametrize("max_roles", [[3], [3, 9], [3, 9, 9]], ids=["one", "two", "three"])
    def test_a_maxrl_shorter_than_the_four_roles_falls_back_to_the_floors(
        self, max_roles: list[int]
    ) -> None:
        """Classic reads `maxrl` all-or-nothing, deliberately unlike the Mantra path's per
        entry read: `bands` pairs it positionally against all four of `ROLE_ORDER`, so a
        short list has no ceiling left to pair with the last role and reading it index by
        index raises `IndexError` out of a pure function nothing downstream catches. `minrl`
        is the fallback, and `min == max` is what the `static` selection already means.

        The case had no test of any kind: every Classic snapshot in this file feeds a full
        four-entry `maxrl`, so relaxing the length test to `len(max_roles) >= 1` survived all
        82 tests across the four files.
        """
        rules, provenance = rules_for_lega(
            role_groups=1, roster_size=25, min_roles=[3, 8, 8, 6], max_roles=max_roles
        )

        assert rules.bands == (("P", 3, 3), ("D", 8, 8), ("C", 8, 8), ("A", 6, 6))
        assert provenance == SNAPSHOT_DECLARED


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


def test_the_provenances_are_distinct_and_greppable() -> None:
    """`rules_for_room`'s rule, extended. Four sources, four exact strings — a reader who
    greps one must not find another, and none is composed at a call site.

    `OPERATOR_DECLARED` was outside this set until 2026-09-24, while `state.py:183` had
    called it "the fourth" since it landed. It is live — `interface/asta.py` returns it
    from three branches — so the set that pins the strings apart had three of the four.
    """
    from fantabot.domain.asta.state import (
        ASSUMED_NOTHING,
        OPERATOR_DECLARED,
        ROOM_DECLARED,
        SNAPSHOT_DECLARED,
    )

    all_four = {ROOM_DECLARED, ASSUMED_NOTHING, SNAPSHOT_DECLARED, OPERATOR_DECLARED}

    assert len(all_four) == 4
    assert not any(a in b for a in all_four for b in all_four if a != b)


class TestTheDeclaredCeiling:
    """`maxrl`, read on the Mantra path at last — the fix's own regression.

    The band is lega 4103937's last sync, the one `test_asta_resize_band`'s docstring
    quotes: `size=32`, `minrl=[2, 23]`, `maxrl=[4, 28]`. `max_goalkeepers()` derives
    `size - min_movement`, which is **9** there against a declared **4**, and nothing
    downstream catches the difference: `optimizer._build_mantra`'s composition guard and
    `reservation.opportunistic_walkaway`'s band gate are the two readers and both fail open,
    `max_bid` reserves credits and checks no role, and `docs/fantalab/01:142` records the
    platform's MAX as client-enforced with no server backstop.
    """

    def test_the_ceilings_are_what_the_lega_declared(self) -> None:
        rules, provenance = rules_for_lega(**LIVE_4103937)  # type: ignore[arg-type]

        assert (rules.max_goalkeepers(), rules.max_movement()) == (4, 28)
        assert provenance == SNAPSHOT_DECLARED

    def test_they_are_read_and_not_arrived_at(self) -> None:
        """The derivation gives 9 and 30 on this band and the lega declares 4 and 28, so what
        this has to assert is the **difference**.

        Its first version compared the derivation to itself — `size`, `min_movement` and
        `min_goalkeepers` are all set by the pre-drift return, so deleting the two
        `max_*_declared=` kwargs changed nothing it read and it passed with the fix fully
        reverted. That is the repo's own "appending a value the list already had" pattern,
        and it is also how the 2026-08-26 band (`minrl == maxrl == [2, 28]` over 30) hid the
        bug in the first place: there the arithmetic and the declaration were one number.
        """
        rules, _ = rules_for_lega(**LIVE_4103937)  # type: ignore[arg-type]

        derived = (rules.size - rules.min_movement, rules.size - rules.min_goalkeepers)
        read = (rules.max_goalkeepers(), rules.max_movement())

        assert derived == (9, 30)
        assert read == (4, 28)
        assert read != derived

    def test_the_tighter_of_the_two_is_what_binds(self) -> None:
        """A declared ceiling is not an override: on a 25-man rosa owing 23 movement players
        there is room for two keepers whatever `maxrl` permits, and 4 would be a ceiling the
        size itself refuses.

        The declaration is asserted alongside, and that is load-bearing rather than
        decoration: 2 and 23 are *also* what the pre-fix derivation returned here, so a test
        reading only the accessors on this band passes with the ceilings never stored — which
        is what the first version of it did.
        """
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=25, min_roles=[2, 23], max_roles=[4, 28]
        )

        assert (rules.max_goalkeepers_declared, rules.max_movement_declared) == (4, 28)
        assert (rules.max_goalkeepers(), rules.max_movement()) == (2, 23)

    def test_a_lega_that_declares_no_maxrl_is_exactly_as_it_was(self) -> None:
        """The whole point of the ceilings being optional: nothing that predates the drift
        changes meaning, and `None` is "nothing was declared", never a zero."""
        rules, provenance = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=None
        )

        assert (rules.max_goalkeepers_declared, rules.max_movement_declared) == (None, None)
        assert (rules.max_goalkeepers(), rules.max_movement()) == (9, 30)
        assert provenance == SNAPSHOT_DECLARED

    def test_a_short_maxrl_is_read_by_the_half_it_has(self) -> None:
        """Per entry, not all-or-nothing. `[4]` states a keeper ceiling and says nothing about
        movement, so the movement half derives — and the keeper half is still honoured.
        Reading `[4]` as "no ceiling at all", which is what the `len(maxrl) >= 2` guard did,
        throws away the tighter of the two constraints because the other one is missing.
        The `IndexError` that guard was really about is answered by the index being *absent*
        rather than by the pair being dropped."""
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[4]
        )

        assert (rules.max_goalkeepers_declared, rules.max_movement_declared) == (4, None)
        assert (rules.max_goalkeepers(), rules.max_movement()) == (4, 30)
        assert rules.size == 32, "one declared half says nothing about the total"

    def test_a_zero_is_an_absence_and_does_not_take_the_other_half_with_it(self) -> None:
        """CLAUDE.md's rule for the other platform, and it holds here: "a room that states 0
        is not stating anything". `domain/lega/parse.py` renders an absent `maxrl` as `()`
        and never as zeros, and a lega permitting zero goalkeepers would contradict the
        platform's own Mantra minimum of two. The 28 beside it is still read."""
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[0, 28]
        )

        assert (rules.max_goalkeepers_declared, rules.max_movement_declared) == (None, 28)
        assert (rules.max_goalkeepers(), rules.max_movement()) == (9, 28)

    def test_a_negative_is_an_absence_too_and_does_not_pin_its_half_to_the_floor(self) -> None:
        """`> 0`, not `!= 0` — the rule is "a number of players", and a `-1` is no more one
        than a 0 is. `domain/lega/parse.py` renders an absent `maxrl` as `()`, so whatever
        else a negative entry might be, a ceiling is the one thing it is not.

        Read literally it is *worse* than a literal 0, because it takes the same route:
        `_satisfiable_ceiling` clamps it up to its own floor, so `maxrl = [-1, 28]` pins the
        keeper half to exactly the two the platform requires and drags the size to 30 with it
        — a band no lega declared, held for a whole auction, out of a field it filled with
        nonsense. The repair report claimed this case in prose ("a missing index, a 0 **or a
        negative** is an absence for that half only") and `!= 0` survived all 82 tests across
        the four files.
        """
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[-1, 28]
        )

        assert (rules.max_goalkeepers_declared, rules.max_movement_declared) == (None, 28)
        assert (rules.max_goalkeepers(), rules.max_movement()) == (9, 28)
        assert rules.size == 32, "nothing was declared for the keepers, so nothing binds"

    def test_the_dataclass_default_declares_nothing_and_never_a_zero(self) -> None:
        """Pinned on the dataclass itself, because nothing else in this file reaches it.

        `test_a_lega_that_declares_no_maxrl_is_exactly_as_it_was` above arrives at the same
        "nothing declared" through `_declared_ceiling`, which returns `None` explicitly — so
        the two field defaults are never exercised, and changing both to `0` passed all 37
        tests here. A bare `RosterRules()` is what `asta bid` plans against when no snapshot
        is read and what every band predating the 2026-09-02 drift still is; under that
        mutant it claims a declared 30-player cap it was never given, and
        `resize_band(RosterRules(), 40)` starts refusing a size the band forbids nowhere.
        """
        plain = RosterRules()

        assert (plain.max_goalkeepers_declared, plain.max_movement_declared) == (None, None)
        assert plain.declared_ceiling_total() is None, "silence is not a 30-man ceiling"
        assert resize_band(plain, 40).size == 40

    def test_one_declared_half_is_not_a_declared_total(self) -> None:
        """`declared_ceiling_total` is what `resize_band` refuses against, and one ceiling
        says nothing about how many the other may supply. Only reachable since the halves are
        read per entry — while a band could carry both or neither, reading that `or` as an
        `and` was a mutation the whole file left alive."""
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[4]
        )

        assert isinstance(rules, RosterRules)
        assert rules.declared_ceiling_total() is None
        assert resize_band(rules, 40).size == 40, "a half-declared band caps nothing"

    def test_a_ceiling_never_reports_below_its_own_floor(self) -> None:
        """`maxrl[0] < minrl[0]` is a band nobody can buy. The floor wins instead of the
        contradiction: "exactly this many" is the one reading that still fills a rosa."""
        rules = RosterRules(
            size=32, min_goalkeepers=2, min_movement=23, max_goalkeepers_declared=1
        )

        assert rules.max_goalkeepers() == 2

    def test_the_total_counts_a_contradictory_ceiling_at_its_floor(self) -> None:
        """`declared_ceiling_total` and `max_goalkeepers()` answer the same question and must
        answer it the same way: a ceiling read up to its floor supplies that many players. A
        total counting the literal 1 reads 29 and refuses a 30 the accessors say this band
        fills."""
        rules = RosterRules(
            size=30,
            min_goalkeepers=2,
            min_movement=23,
            max_goalkeepers_declared=1,
            max_movement_declared=28,
        )

        assert rules.declared_ceiling_total() == 30
        assert resize_band(rules, 30).size == 30


class TestCeilingsThatCannotFillTheDeclaredSize:
    """`sum(maxrl) < xsltc` is not a lega contradicting itself.

    `xsltc` is a *maximum* roster size and `maxrl` caps each half, so a pair supplying fewer
    players than `xsltc` means the rosa stops where the ceilings stop. That is a real,
    fieldable configuration and the buyable reading is the smaller number — so the ceilings
    bind the total, and `rules_for_lega` clamps `size` to what they supply.

    The first version of this fix discarded **both** ceilings there and still returned
    `SNAPSHOT_DECLARED`, so `asta optimize` printed *"read from the lega's last sync"* over
    the 9-keeper derivation the lega never declared — the original defect verbatim, one notch
    further along. The margin on the live lega is **zero** (`4 + 28 = 32 = xsltc`) and the
    test was `<`, not `<=`: one added to `xsltc`, or one taken off either ceiling, turned the
    cap back off in silence. CLAUDE.md records that this lega's roster settings already moved
    under us once with nothing noticing.
    """

    def test_one_more_player_in_xsltc_does_not_turn_the_ceiling_off(self) -> None:
        """An admin raising `xsltc` from 32 to 33. Under the discarding guard this returned
        `max_goalkeepers() == 10` and `max_movement() == 31` against a declared 4 and 28."""
        rules, provenance = rules_for_lega(
            role_groups=2, roster_size=33, min_roles=[2, 23], max_roles=[4, 28]
        )

        assert isinstance(rules, RosterRules)
        assert (rules.max_goalkeepers(), rules.max_movement()) == (4, 28)
        assert rules.size == 32, "4 + 28 is every player this band can supply"
        assert provenance == SNAPSHOT_DECLARED

    def test_one_fewer_keeper_in_maxrl_does_not_turn_the_ceiling_off(self) -> None:
        """The other side of the zero margin: `xsltc` is untouched and `maxrl[0]` drops by
        one. Under the discarding guard this returned `max_goalkeepers() == 9`."""
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[3, 28]
        )

        assert rules.max_goalkeepers() == 3
        assert rules.size == 31

    def test_a_ceiling_under_its_own_floor_lands_on_the_floor_and_the_size_follows(
        self,
    ) -> None:
        """`maxrl = [2, 20]` under `minrl = [2, 23]` — at most 20 movement players and at
        least 23 — is the one shape here that really is a contradiction. The floor wins per
        half, and the total follows it: 2 + 23 is 25, and `xsltc = 32` is a maximum this band
        cannot reach. Still `SNAPSHOT_DECLARED`, and honestly so — every number in it was
        read from the sync."""
        rules, provenance = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[2, 20]
        )

        assert (rules.max_goalkeepers(), rules.max_movement()) == (2, 23)
        assert rules.size == 25
        assert provenance == SNAPSHOT_DECLARED

    @pytest.mark.parametrize(
        "snapshot",
        [
            {"role_groups": 2, "roster_size": 33, "min_roles": [2, 23], "max_roles": [4, 28]},
            {"role_groups": 2, "roster_size": 32, "min_roles": [2, 23], "max_roles": [3, 28]},
            {"role_groups": 2, "roster_size": 32, "min_roles": [2, 23], "max_roles": [2, 20]},
        ],
        ids=["xsltc one too high", "one keeper fewer", "ceiling under its floor"],
    )
    def test_the_clamped_band_can_always_fill_itself(self, snapshot: dict) -> None:
        """What the discarding guard was protecting, kept without discarding anything:
        `optimize_roster` fills `rules.size` exactly and skips a candidate whose half is at
        its ceiling, so a band supplying fewer than `size` players raises `InfeasibleRoster`
        once per two-second cycle for the evening. Clamping the size is what makes that
        unreachable — and it holds the floors too, which is why the ceilings are counted at
        `_satisfiable_ceiling` and not literally."""
        rules, _ = rules_for_lega(**snapshot)

        assert isinstance(rules, RosterRules)
        assert rules.max_goalkeepers() + rules.max_movement() >= rules.size
        assert rules.size >= rules.min_goalkeepers + rules.min_movement
        assert rules.max_goalkeepers() >= rules.min_goalkeepers
        assert rules.max_movement() >= rules.min_movement


class TestTheCeilingUnderAResize:
    """`asta bid --size` replaces a *lega's* total with a *room's* (`resize_band`). The
    ceilings have to survive that as `maxrl` and not as arithmetic."""

    def _live(self) -> RosterRules:
        rules, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=[4, 28]
        )
        assert isinstance(rules, RosterRules)
        return rules

    def test_a_shrink_keeps_the_ceiling_and_lets_the_size_bind_instead(self) -> None:
        out = resize_band(self._live(), 25)

        assert (out.max_goalkeepers_declared, out.max_movement_declared) == (4, 28)
        assert (out.max_goalkeepers(), out.max_movement()) == (2, 23)
        assert out.max_goalkeepers() >= out.min_goalkeepers
        assert out.max_movement() >= out.min_movement

    def test_a_growth_inside_the_declared_band_still_reports_the_declaration(self) -> None:
        """A ceiling is a rule about the rosa, not about its total: grown to the band's own
        32 the keeper ceiling is still 4, not the 30 the size would allow."""
        out = resize_band(self._live(), 30)

        assert (out.max_goalkeepers(), out.max_movement()) == (4, 28)

    def test_a_size_the_declared_band_fills_exactly_is_allowed(self) -> None:
        """The boundary, and it is this lega's own total: `4 + 28` supplies 32 and `xsltc` is
        32. The refusal below is `>` and a `>=` would refuse the one size the band was
        written for — a mutation nothing in this file caught until now."""
        out = resize_band(self._live(), 32)

        assert out.size == 32
        assert out.max_goalkeepers() + out.max_movement() == 32

    def test_a_size_the_declared_band_cannot_fill_is_refused(self) -> None:
        """The Classic refusal, reached by the Mantra route. `4 + 28` supplies 32 players
        and no more, so a 40 is a roster this band can never fill — said here, at the flag,
        rather than as an `InfeasibleRoster` from inside the bidding loop an hour later.

        **The whole message, because the number in it is the only thing the operator can act
        on.** This asserted `match=r"cannot be filled"` until 2026-09-24, and that phrase is
        emitted by *both* raise sites in `resize_band` — so it pinned neither branch nor any
        number, and rendering the refused `size` where the ceiling belongs survived all 37
        tests in this file and all 82 across its three siblings. Under that mutant an
        operator who typed `--size 40` reads "this band allows at most 40 players", which
        contradicts the refusal in the same breath and names the wrong one of the two numbers
        he is fighting. The docstring above already made the numeric claim; now something
        checks it.
        """
        rules = self._live()
        assert rules.declared_ceiling_total() == 32, "`4 + 28` is the number it must name"

        with pytest.raises(ValueError) as refusal:
            resize_band(rules, 40)

        assert str(refusal.value) == (
            "--size 40 cannot be filled: this band allows at most 32 players"
        )

    def test_the_two_refusals_each_name_their_own_ceiling(self) -> None:
        """Which of the two sites refused is readable from the number, and that is what
        disambiguates the shared phrase. Classic counts `sum(max_of(role))` — 25 on the
        default band — and Mantra counts `declared_ceiling_total()`; a `match=` that both
        satisfy is a `match=` pinning neither.
        """
        with pytest.raises(ValueError) as mantra:
            resize_band(self._live(), 40)
        with pytest.raises(ValueError) as classic:
            resize_band(ClassicRosterRules(), 40)

        assert str(mantra.value).endswith("at most 32 players")
        assert str(classic.value).endswith("at most 25 players")

    def test_a_band_with_no_declared_ceiling_resizes_exactly_as_it_did(self) -> None:
        """The refusal above can only bite where a lega actually sent `maxrl`; derived
        ceilings always leave room for `size` players."""
        plain, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=None
        )

        assert resize_band(plain, 40).size == 40


#: One Por/A/A schema, so `_build_mantra` seeds a legal XI and then fills greedily.
XI_SCHEMA = {
    "por-a-a": SchemaLegality(
        nome="por-a-a",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
            SlotRule("A2", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}
#: Three keepers and four attackers, the keepers carrying the higher signal — so value-first
#: greedy wants them and only a ceiling stands in the way.
FIVE_MAN_POOL = [MantraPlayer(id=f"gk{i}", roles=normalize_roles(["POR"])) for i in (1, 2, 3)] + [
    MantraPlayer(id=f"a{i}", roles=normalize_roles(["A"])) for i in (1, 2, 3, 4)
]
FIVE_MAN_VALUE = NaiveValueModel(
    signals={"gk1": 10.0, "gk2": 10.0, "gk3": 10.0, "a1": 5.0, "a2": 5.0, "a3": 5.0, "a4": 5.0},
    prior_mean=1.0,
    base_variance=4.0,
    no_history_variance=4.0,
)


def _keepers_planned(max_roles: list[int] | None, roster_size: int = 5) -> int:
    """How many goalkeepers `optimize_roster` drafts for a lega declaring `max_roles` over
    `roster_size`. `min_roles=[1, 1]` derives a ceiling of `roster_size - 1`, so `[1, 4]` is
    the only thing that can hold the plan to one."""
    rules, _ = rules_for_lega(
        role_groups=2, roster_size=roster_size, min_roles=[1, 1], max_roles=max_roles
    )
    roster = optimize_roster(
        AstaState(total_budget=100.0),
        FIVE_MAN_POOL,
        value=FIVE_MAN_VALUE,
        prices={p.id: 10.0 for p in FIVE_MAN_POOL},
        teams={p.id: p.id for p in FIVE_MAN_POOL},  # one club each: no covariance to chase
        legality=XI_SCHEMA,
        rules=rules,
        lam=0.0,
    ).optimal
    assert len(roster) == rules.size, "the band must still fill, ceiling or no ceiling"
    return sum(1 for pid in roster.player_ids if pid.startswith("gk"))


class TestTheCeilingWhenASlotIsConsumed:
    """`drop_unvaluable` sets aside an owned player the pool cannot name and shrinks the band
    by one, because a slot he fills is a slot we must not plan to buy again. The declared
    ceiling is part of that band."""

    def test_the_declared_movement_ceiling_moves_with_its_floor(self) -> None:
        """`maxrl` counts the whole rosa and he is in it, so a ceiling left alone would let
        the plan buy its full movement allowance *on top of* him: 28 more, plus him, past a
        declared 28. The movement half is what shrinks for the same reason the floor's does
        — his role is exactly what we do not know."""
        rules, _ = rules_for_lega(**LIVE_4103937)  # type: ignore[arg-type]

        _kept, shrunk, dropped = drop_unvaluable(
            AstaState(owned=("7581",), total_budget=500.0), [], rules
        )

        assert dropped == ["7581"]
        assert isinstance(shrunk, RosterRules)
        assert (shrunk.size, shrunk.min_movement) == (31, 22)
        assert shrunk.max_movement_declared == 27
        assert shrunk.max_goalkeepers_declared == 4, "one slot cannot come out of both halves"

    def test_the_band_can_still_fill_itself(self) -> None:
        """Feasibility is what this has to preserve: both the size and the ceiling fall by
        the same count, so the headroom between them is unchanged — zero on this lega, where
        `4 + 28` is exactly `xsltc`. Below zero the optimizer runs out of legal candidates
        and raises, per cycle, for the evening.

        Asserted as that difference and not as `>=`, which was its first form and could not
        fail: deleting the ceiling shrink can only *raise* the ceiling, so `4 + 28 >= 30`
        held exactly as `4 + 26 >= 30` did.
        """
        rules, _ = rules_for_lega(**LIVE_4103937)  # type: ignore[arg-type]
        headroom_before = rules.max_goalkeepers() + rules.max_movement() - rules.size

        _kept, shrunk, _dropped = drop_unvaluable(
            AstaState(owned=("7581", "9999"), total_budget=500.0), [], rules
        )

        assert isinstance(shrunk, RosterRules)
        assert headroom_before == 0
        assert shrunk.max_goalkeepers() + shrunk.max_movement() - shrunk.size == headroom_before

    def test_a_band_with_no_declared_ceiling_shrinks_exactly_as_it_did(self) -> None:
        plain, _ = rules_for_lega(
            role_groups=2, roster_size=32, min_roles=[2, 23], max_roles=None
        )

        _kept, shrunk, _dropped = drop_unvaluable(
            AstaState(owned=("7581",), total_budget=500.0), [], plain
        )

        assert isinstance(shrunk, RosterRules)
        assert shrunk.max_movement_declared is None
        assert (shrunk.size, shrunk.min_movement) == (31, 22)


class TestWhatTheCeilingActuallyStops:
    """The bug as the optimizer sees it: a plan that drafts keepers the rosa may not hold.

    A five-man world under one Por/A/A schema, so `_build_mantra` seeds a legal XI and then
    fills two slots greedily. The keepers carry the higher signal, so value-first greedy
    wants them, and `min_roles=[1, 1]` derives a ceiling of four — the declared `[1, 4]` is
    the only thing in the way.
    """

    def test_the_plan_stops_at_the_keepers_the_lega_permits(self) -> None:
        assert _keepers_planned([1, 4]) == 1

    def test_and_without_the_declaration_it_drafts_more_than_that(self) -> None:
        """The control, and it passes either way on purpose: it is what makes the 1 above a
        ceiling binding rather than a world too small to hold a second keeper. Named here
        because it is the one test in this file that no mutation of `state.py` can turn red,
        and a control is the only honest reason for that."""
        assert _keepers_planned(None) > 1

    def test_a_ceiling_short_of_the_declared_size_still_stops_the_plan(self) -> None:
        """Through the optimizer, because that is where the discarding guard was paid for: a
        `roster_size` of 6 against a `[1, 4]` supplying 5 used to restore the derived keeper
        ceiling of five, and the plan drafted every keeper in the world. Clamped, the band is
        five players with one keeper — and it still fills, which is what the guard was for.
        """
        assert _keepers_planned([1, 4], roster_size=6) == 1
