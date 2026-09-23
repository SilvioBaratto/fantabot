"""The Mantra auto-sub engine: whoever comes on, the XI that results, and what it costs. Pure.

`rules/sistema-mantra.md` §Substitution System, in full — the combination order driven by
bench position, the keeper going first, the man-short fallback, the three tiers (Optimal,
Efficient, Adapted) and the three modes (BASIC, EASY, MASTER).

The roster is literal and fields 343, whose slots are
`POR, {B,DC}, DC, DC, E, C, {C,M}, E, {A,W}, {A,PC}, {A,W}`.

The mode tests use a second roster and the pair 343 / 352, which share a defence and differ
in midfield (`352` is `… E, C, M, {C,M}, {E,W} …`). That pair is what makes the three modes
observably different rather than three names for one search.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup import positional, schema
from fantabot.domain.lineup.substitution import (
    SUB_MODES,
    SubstitutionEngine,
    bench_combinations,
    parse_sub_mode,
    substitute,
)

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
        """Two centre-backs are missing and one sits on the bench. `343`'s three defensive
        slots admit defenders and nothing else — not even at `-1*` — so no tier fields
        eleven, the engine drops to replacing one, and a centre-back slot stays empty.

        Before T20 this test read the *`E`* pair the same way, which the Adapted tier then
        disproved: a centre-back takes an `E` slot for `-1`, so that XI was eleven men with
        one malus and not ten. The rule survived the correction; the example did not.
        """
        outcome = _substitute(absent=(20, 30))

        assert outcome.entered == (3,)
        assert outcome.short == 1
        assert outcome.malus == 0
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
        outcome = _substitute(absent=(20, 30))

        assert {pid for pid in outcome.fielded if pid is not None} == (
            set(STARTS) - {20, 30} | {3}
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


# -- T20: the tiers and the modes -----------------------------------------------------
#
# A second roster, because the modes are only observable when two modules are on the table.
# It fields 343 exactly: a keeper, three centre-backs, two `E`, two central midfielders and
# three forwards. `352` takes the same defence and asks for an `M` and an `E/W` where 343
# asks for a second `E` and a third forward — so an incoming `M` fields 352 and never 343.
MODE_STARTS = (0, 11, 12, 13, 21, 31, 32, 22, 41, 42, 43)
MODE_ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}),
    11: frozenset({"DC"}), 12: frozenset({"DC"}), 13: frozenset({"DC"}),
    21: frozenset({"E"}), 22: frozenset({"E"}),
    31: frozenset({"C"}), 32: frozenset({"C"}),
    41: frozenset({"A"}), 42: frozenset({"A"}), 43: frozenset({"A"}),
}
#: The bench, in entry order: **the midfielder first**, the spare forward second. That order
#: is the whole experiment — MASTER takes the earlier man and changes the module for him,
#: BASIC keeps the module and takes the later one.
MODE_BENCH_ROLES: dict[int, frozenset[str]] = {
    51: frozenset({"M"}),
    52: frozenset({"A"}),
}
MODE_BENCH = (51, 52)
MODE_ALL = MODE_ROLES | MODE_BENCH_ROLES
BOTH = ("343", "352")


def _mode_substitute(absent: tuple[int, ...], **over: object) -> object:
    voted = [pid for pid in (*MODE_STARTS, *MODE_BENCH) if pid not in absent]
    return substitute(
        module="343", starts=MODE_STARTS, bench=MODE_BENCH, voted=voted, roles=MODE_ALL,
        **over,  # type: ignore[arg-type]
    )


class TestParsingTheMode:
    @pytest.mark.parametrize("raw", ["basic", "EASY", " Master ", "MASTER"])
    def test_a_known_mode_survives_case_and_space(self, raw: str) -> None:
        assert parse_sub_mode(raw) in SUB_MODES

    @pytest.mark.parametrize("raw", [None, "", "   ", "adapted", "BASIC "[:0], "optimal"])
    def test_anything_else_is_none_and_never_a_default(self, raw: str | None) -> None:
        """Fail closed (AD4). Guessing BASIC would make an unanswered Open Question look
        answered, and the three modes field different XIs — which the tests below measure."""
        assert parse_sub_mode(raw) is None


class TestTheThreeModesDisagree:
    """One absence, one bench, three answers. If these ever agree the modes are a fiction."""

    def test_basic_keeps_the_module_and_takes_the_later_bench_man(self) -> None:
        outcome = _mode_substitute(absent=(43,), mode="basic", modules=BOTH)

        assert (outcome.module, outcome.entered) == ("343", (52,))
        assert (outcome.malus, outcome.tier) == (0, "optimal")

    def test_master_changes_the_module_to_take_the_earlier_one(self) -> None:
        """`rules/sistema-mantra.md`: "bench order dominates over keeping the original
        formation". The `M` is bench position 0 and fields only 352, so MASTER fields 352."""
        outcome = _mode_substitute(absent=(43,), mode="master", modules=BOTH)

        assert (outcome.module, outcome.entered) == ("352", (51,))
        assert (outcome.malus, outcome.tier) == (0, "efficient")

    def test_easy_never_changes_the_module(self) -> None:
        """With only the midfielder left on the bench, BASIC reaches the Efficient tier and
        fields 352 for nothing; EASY has no Efficient tier and pays the `-1` instead."""
        absent = (43, 52)

        easy = _mode_substitute(absent=absent, mode="easy", modules=BOTH)
        basic = _mode_substitute(absent=absent, mode="basic", modules=BOTH)

        assert (easy.module, easy.entered, easy.malus, easy.tier) == ("343", (51,), 1, "adapted")
        assert (basic.module, basic.entered, basic.malus) == ("352", (51,), 0)

    def test_easy_never_changes_the_module_even_when_it_would_be_free(self) -> None:
        outcome = _mode_substitute(absent=(43,), mode="easy", modules=BOTH)

        assert outcome.module == "343"

    @pytest.mark.parametrize("mode", ["basic", "easy", "master"])
    def test_no_module_but_the_original_is_ever_fielded_without_mods(self, mode: str) -> None:
        """`modules` defaults to empty — the lega's own `mods` is the only licence to move.
        A module the lega does not allow is one the platform refuses."""
        outcome = _mode_substitute(absent=(43, 52), mode=mode)  # type: ignore[arg-type]

        assert outcome.module == "343"


class TestTheAdaptedTier:
    def test_a_malus_fields_eleven_where_natural_roles_field_ten(self) -> None:
        """Both `E` are missing and a centre-back and an `E` are on the bench. 343's `E`
        slot takes a centre-back for `-1`, so the Adapted tier fields eleven with one malus
        rather than ten with none — the substitution the platform actually makes."""
        outcome = _substitute(absent=(40, 70))

        assert outcome.entered == (3, 4)
        assert (outcome.short, outcome.malus, outcome.tier) == (0, 1, "adapted")

    def test_the_malus_is_a_count_and_not_an_identity(self) -> None:
        """`rules/sistema-mantra.md`'s own gotcha: the platform assigns the penalty "in no
        particular order" among interchangeable players. Only the count is reproducible, so
        nothing here — and nothing downstream — may read which man carries it."""
        outcome = _substitute(absent=(40, 70))

        assert outcome.malus == 1
        assert {pid for pid in outcome.fielded if pid is not None} == (
            set(STARTS) - {40, 70} | {3, 4}
        )

    def test_the_cheapest_adapted_fit_wins_over_an_earlier_dearer_one(self) -> None:
        """Bench order breaks ties; it does not outrank the malus count. `1` is the first
        bench man and costs two maluses here, `(3, 4)` costs one, so `(3, 4)` is fielded."""
        outcome = _substitute(absent=(40, 70))

        assert outcome.malus == 1
        assert 1 not in outcome.entered


class TestTheStarredCells:
    def test_the_engine_may_place_a_starred_role_and_submission_may_not(self) -> None:
        """`-1*` is admitted here and only here. 343's `{B,DC}` slot takes a `DD` after a
        forced substitution and refuses him at submission — so the same XI that the engine
        legally fields would be refused if it were *submitted*."""
        slot = schema.admissions("343")[1]

        assert "DD" not in slot.submission
        assert "DD" in slot.substitution

    def test_every_slot_orders_the_three_sets(self) -> None:
        for code in sorted(schema.modules()):
            for index, slot in enumerate(schema.admissions(code)):
                assert slot.natural <= slot.submission <= slot.substitution, (code, index)

    def test_the_four_one_four_one_w_t_exception_comes_from_the_shipped_matrix(self) -> None:
        """`rules/sistema-mantra.md`: W and T are interchangeable with a malus *except* in
        4-1-4-1, where neither may take the other's slot at all. 4-2-3-1 is the control —
        its `T` slot does take a `W`, at `-1*`."""
        t_slot = schema.admissions("4141")[7]
        w_slot = schema.admissions("4141")[6]
        control = schema.admissions("4231")[8]

        assert t_slot.natural == frozenset({"T"}) and "W" not in t_slot.substitution
        assert w_slot.natural == frozenset({"W"}) and "T" not in w_slot.substitution
        assert control.natural == frozenset({"T"}) and "W" in control.substitution

    def test_a_starred_placement_is_what_the_submission_guard_refuses(self) -> None:
        """The two rules are one rule seen twice: what `positional` refuses at submission is
        exactly a role the slot admits only as `substitution`."""
        starter_roles = [MODE_ALL[pid] for pid in MODE_STARTS]
        starter_roles[1] = frozenset({"DD"})

        assert positional.refusal("343", starter_roles) != ""
        assert positional.refusal("343", [MODE_ALL[pid] for pid in MODE_STARTS]) == ""


class TestTheMemoizedEngine:
    def test_the_same_absence_pattern_returns_the_same_answer(self) -> None:
        engine = SubstitutionEngine(
            module=MODULE, starts=STARTS, bench=BENCH, roles=ALL_ROLES, max_subs=5
        )
        voted = [pid for pid in (*STARTS, *BENCH) if pid != 80]

        first = engine.field_xi(voted)
        second = engine.field_xi(list(reversed(voted)))

        assert first is second

    def test_a_different_pattern_is_computed_afresh(self) -> None:
        engine = SubstitutionEngine(
            module=MODULE, starts=STARTS, bench=BENCH, roles=ALL_ROLES, max_subs=5
        )

        one = engine.field_xi([pid for pid in (*STARTS, *BENCH) if pid != 80])
        two = engine.field_xi([pid for pid in (*STARTS, *BENCH) if pid != 90])

        assert one is not two
        assert one.entered == two.entered == (1,)

    def test_an_absent_bench_man_is_part_of_the_key(self) -> None:
        """Two patterns with the same missing starters and different missing bench must not
        share an answer — the bench is what the engine picks from."""
        engine = SubstitutionEngine(
            module=MODULE, starts=STARTS, bench=BENCH, roles=ALL_ROLES, max_subs=5
        )

        plain = engine.field_xi([pid for pid in (*STARTS, *BENCH) if pid != 80])
        thinned = engine.field_xi([pid for pid in (*STARTS, *BENCH) if pid not in (80, 1)])

        assert plain.entered == (1,)
        assert thinned.entered == (2,)

    def test_the_cache_is_per_instance(self) -> None:
        """A module-level cache would carry one lega's roster into the next."""
        first = SubstitutionEngine(module=MODULE, starts=STARTS, bench=BENCH, roles=ALL_ROLES)
        second = SubstitutionEngine(module=MODULE, starts=STARTS, bench=BENCH, roles=ALL_ROLES)
        voted = [pid for pid in (*STARTS, *BENCH) if pid != 80]

        assert first.field_xi(voted) is not second.field_xi(voted)
