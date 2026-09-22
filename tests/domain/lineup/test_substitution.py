"""The Mantra auto-sub engine, core: whoever comes on, and the XI that results. Pure.

`rules/sistema-mantra.md` §Substitution System. This file covers what every mode shares —
the combination order driven by bench position, the keeper going first, the Optimal tier
(the original schema, natural roles, no malus) and the man-short fallback. The Efficient and
Adapted tiers and the three modes are T20's.

The roster is literal and fields 343, whose slots are
`POR, {B,DC}, DC, DC, E, C, {C,M}, E, {A,W}, {A,PC}, {A,W}`.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.substitution import bench_combinations, substitute

MODULE = "343"
#: The XI in slot order: keeper, three centre-backs, two E, two central midfielders, three
#: forwards. Ids are `slot * 10` so a fielded slot is readable at a glance.
STARTS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}),
    10: frozenset({"DC"}), 20: frozenset({"DC"}), 30: frozenset({"DC"}),
    40: frozenset({"E"}), 70: frozenset({"E"}),
    50: frozenset({"C"}), 60: frozenset({"C", "M"}),
    80: frozenset({"A"}), 90: frozenset({"A"}), 100: frozenset({"A"}),
}
#: The bench, in entry order: two forwards, a centre-back, an `E`, **two** keepers and a
#: regista. Two keepers, because the earlier one is the one who takes the shirt.
BENCH_ROLES: dict[int, frozenset[str]] = {
    1: frozenset({"A"}), 2: frozenset({"A"}), 3: frozenset({"DC"}),
    4: frozenset({"E"}), 5: frozenset({"POR"}), 6: frozenset({"M"}),
    7: frozenset({"POR"}),
}
BENCH = (1, 2, 3, 4, 5, 6, 7)
ALL_ROLES = ROLES | BENCH_ROLES


def _substitute(absent: tuple[int, ...] = (), **over: object) -> object:
    voted = [pid for pid in (*STARTS, *BENCH) if pid not in absent]
    return substitute(
        module=MODULE, starts=STARTS, bench=BENCH, voted=voted, roles=ALL_ROLES,
        **over,  # type: ignore[arg-type]
    )


class TestTheCombinationOrder:
    def test_the_worked_example_is_exact(self) -> None:
        """`rules/sistema-mantra.md`: three of five, A-B-C-D-E, in this exact priority."""
        assert bench_combinations(["A", "B", "C", "D", "E"], 3) == [
            ("A", "B", "C"), ("A", "B", "D"), ("A", "B", "E"), ("A", "C", "D"),
            ("A", "C", "E"), ("A", "D", "E"), ("B", "C", "D"), ("B", "C", "E"),
            ("B", "D", "E"), ("C", "D", "E"),
        ]

    def test_no_replacements_is_one_empty_combination(self) -> None:
        assert bench_combinations(["A", "B"], 0) == [()]

    def test_more_replacements_than_bench_is_nothing(self) -> None:
        assert bench_combinations(["A", "B"], 3) == []


class TestTheEngineRuns:
    def test_a_full_house_is_fielded_as_submitted(self) -> None:
        """Every starter voted, so the engine never runs — the lineup stands."""
        outcome = _substitute()

        assert outcome.fielded == STARTS
        assert outcome.entered == ()
        assert outcome.short == 0

    def test_a_lineup_already_out_of_position_is_scored_as_submitted(self) -> None:
        """The rules doc: with every starter voted the engine does not run at all, and the
        lineup stands "exactly as submitted, malus included" — even where the platform let
        an out-of-position pair through. Re-matching here would quietly undo the malus the
        manager is owed, and field an XI nobody sent."""
        swapped = (0, 10, 20, 30, 60, 50, 40, 70, 80, 90, 100)  # the `E` and the `C/M` crossed

        outcome = substitute(
            module=MODULE, starts=swapped, bench=BENCH,
            voted=(*swapped, *BENCH), roles=ALL_ROLES,
        )

        assert outcome.fielded == swapped
        assert outcome.entered == ()

    def test_a_bench_player_without_a_vote_cannot_come_on(self) -> None:
        """Bench 1 is missing too, so the first forward who can come on is bench 2."""
        voted = [pid for pid in (*STARTS, *BENCH) if pid not in (80, 1)]

        outcome = substitute(
            module=MODULE, starts=STARTS, bench=BENCH, voted=voted, roles=ALL_ROLES
        )

        assert outcome.entered == (2,)


class TestTheKeeperGoesFirst:
    def test_the_reserve_keeper_replaces_him(self) -> None:
        outcome = _substitute(absent=(0,))

        assert outcome.entered == (5,)
        assert outcome.fielded[0] == 5
        assert outcome.short == 0

    def test_the_earlier_of_two_reserve_keepers_takes_the_shirt(self) -> None:
        """Bench order decides, and 7 is a keeper too."""
        outcome = _substitute(absent=(0,))

        assert outcome.entered == (5,)
        assert 7 not in outcome.entered

    def test_with_no_reserve_keeper_the_slot_stays_empty(self) -> None:
        voted = [pid for pid in (*STARTS, *BENCH) if pid not in (0, 5, 7)]

        outcome = substitute(
            module=MODULE, starts=STARTS, bench=BENCH, voted=voted, roles=ALL_ROLES
        )

        assert outcome.fielded[0] is None
        assert outcome.short == 1
        assert 5 not in outcome.entered

    def test_he_is_replaced_before_the_outfield(self) -> None:
        """Keeper and a forward missing: the keeper's sub is settled first and the forward's
        is drawn from the rest of the bench."""
        outcome = _substitute(absent=(0, 80))

        assert outcome.entered[0] == 5
        assert outcome.fielded[0] == 5
        assert set(outcome.entered) == {5, 1}


class TestTheOptimalTier:
    def test_the_earliest_bench_player_who_fits_comes_on(self) -> None:
        outcome = _substitute(absent=(80,))

        assert outcome.entered == (1,)
        assert outcome.short == 0
        assert set(outcome.fielded) == set(STARTS) - {80} | {1}

    def test_a_bench_player_whose_role_does_not_fit_is_passed_over(self) -> None:
        """A centre-back is missing: bench 1 and 2 are forwards, so the engine reaches 3."""
        outcome = _substitute(absent=(20,))

        assert outcome.entered == (3,)

    def test_two_missing_take_the_first_combination_that_fits(self) -> None:
        outcome = _substitute(absent=(80, 90))

        assert outcome.entered == (1, 2)

    def test_the_combination_order_beats_bench_order_alone(self) -> None:
        """A forward and a centre-back are missing. `(1, 2)` is the first combination and
        cannot cover the back line, so the engine walks on to `(1, 3)` — a pairing, not two
        independent picks."""
        outcome = _substitute(absent=(20, 80))

        assert outcome.entered == (1, 3)

    def test_the_survivors_may_move_slots_for_the_incomer(self) -> None:
        """The pure `C` in the `{C}` slot is missing, and the only midfielder left on the
        bench is an `M`, who cannot take that slot. So `60` (a `C/M`) moves into it and the
        incomer takes `60`'s `{C,M}` slot: the engine reallocates all eleven rather than
        patching the one hole."""
        outcome = _substitute(absent=(50,))

        assert outcome.entered == (6,)
        assert outcome.fielded[5] == 60
        assert outcome.fielded[6] == 6
        assert set(outcome.fielded) == set(STARTS) - {50} | {6}


class TestTheManShortFallback:
    def test_with_nothing_that_fits_the_team_plays_one_fewer(self) -> None:
        """Both `E` are missing and one `E` sits on the bench. No pair of bench players
        fields eleven — every other role's slots are already full — so the engine drops to
        replacing one, and the second flank stays empty."""
        outcome = _substitute(absent=(40, 70))

        assert outcome.entered == (4,)
        assert outcome.short == 1
        assert outcome.fielded.count(None) == 1

    def test_an_empty_bench_leaves_every_hole_open(self) -> None:
        voted = [pid for pid in STARTS if pid != 80]

        outcome = substitute(
            module=MODULE, starts=STARTS, bench=BENCH, voted=voted, roles=ALL_ROLES
        )

        assert outcome.entered == ()
        assert outcome.short == 1
        assert None in outcome.fielded

    def test_the_men_who_stay_are_still_fielded(self) -> None:
        outcome = _substitute(absent=(40, 70))

        assert {pid for pid in outcome.fielded if pid is not None} == (
            set(STARTS) - {40, 70} | {4}
        )


class TestTheSubstitutionCap:
    def test_the_cap_counts_the_keeper_s_replacement(self) -> None:
        """`ssnum` covers every substitution, the keeper's included: with one allowed and
        both a keeper and a forward missing, only the keeper is replaced."""
        outcome = _substitute(absent=(0, 80), max_subs=1)

        assert outcome.entered == (5,)
        assert outcome.short == 1

    def test_no_substitutions_allowed_fields_the_survivors(self) -> None:
        outcome = _substitute(absent=(80,), max_subs=0)

        assert outcome.entered == ()
        assert outcome.short == 1

    def test_the_cap_bites_only_when_it_is_below_the_need(self) -> None:
        outcome = _substitute(absent=(80, 90), max_subs=5)

        assert outcome.entered == (1, 2)
        assert outcome.short == 0

    @pytest.mark.parametrize("cap", [-1, -5])
    def test_a_negative_cap_is_refused(self, cap: int) -> None:
        with pytest.raises(ValueError, match="max_subs"):
            _substitute(absent=(80,), max_subs=cap)


class TestWhatItRefuses:
    def test_an_unknown_module_is_refused(self) -> None:
        with pytest.raises(ValueError, match="module"):
            substitute(
                module="999", starts=STARTS, bench=BENCH, voted=STARTS, roles=ALL_ROLES
            )

    def test_a_lineup_that_is_not_eleven_is_refused(self) -> None:
        with pytest.raises(ValueError, match="eleven"):
            substitute(
                module=MODULE, starts=STARTS[:10], bench=BENCH, voted=STARTS, roles=ALL_ROLES
            )
