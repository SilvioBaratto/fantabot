"""`build` — max-weight assignment per module, argmax module, cross-checked against legality.

The builder's job is an exact optimum (Hungarian) and a positionally-legal `starts[]`. Two
things are pinned: it maximises `sum(value)` within and across modules, and its output is
independently confirmed fieldable by `domain/asta/legality` — the check that would have
caught the live `LUP009`.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.legality import build_legality, fieldable_schemi, load_compat
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.lineup import positional
from fantabot.domain.lineup.build import (
    lineup_for_module,
    place_all_with_malus,
    ranked_lineups,
)
from fantabot.domain.lineup.models import RosterPlayer

MODULES = ["3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"]


def _p(pid: int, fvmma: float, *roles: str) -> RosterPlayer:
    return RosterPlayer(id=pid, roles=frozenset(roles), fvmma=fvmma)


# A roster whose natural roles field only 3-4-3: no T, no Ds/Dd, one pure striker.
GOLDEN = [
    _p(6482, 6.0, "POR"),
    _p(2788, 8.0, "DC"),
    _p(7564, 7.0, "DC"),
    _p(7274, 6.0, "DC"),
    _p(7181, 7.0, "E"),
    _p(1850, 6.0, "M"),
    _p(5504, 6.0, "C"),
    _p(5678, 5.0, "E"),
    _p(4179, 10.0, "W"),
    _p(6875, 9.0, "A"),
    _p(2194, 5.0, "W"),
]
GOLDEN_VALUE = {p.id: p.fvmma for p in GOLDEN}


def test_picks_the_only_fieldable_module_and_starts_with_the_keeper() -> None:
    module, starts = ranked_lineups(GOLDEN, MODULES, value=GOLDEN_VALUE)[0]

    assert module == "343"
    assert set(starts) == {p.id for p in GOLDEN}
    assert starts[0] == 6482  # GK is starts[0]


def test_maximises_score_within_a_module_benching_the_weaker_same_role_player() -> None:
    weak_dc = _p(9999, 1.0, "DC")
    roster = [*GOLDEN, weak_dc]
    value = {**GOLDEN_VALUE, 9999: 1.0}

    module, starts = ranked_lineups(roster, MODULES, value=value)[0]

    assert module == "343"
    assert 9999 not in starts  # the three stronger DCs are preferred


def test_a_roster_with_no_keeper_fields_no_module() -> None:
    outfield_only = [p for p in GOLDEN if "POR" not in p.roles]

    assert ranked_lineups(outfield_only, MODULES, value=GOLDEN_VALUE) == []


# A roster of universal outfielders + two keepers can field every module.
_UNIVERSAL = ("DC", "DS", "DD", "B", "E", "M", "C", "W", "T", "A", "PC")
UNIVERSAL = [_p(1, 5.0, "POR"), _p(2, 5.0, "POR")] + [
    _p(100 + i, float(i + 1), *_UNIVERSAL) for i in range(13)
]
UNIVERSAL_VALUE = {p.id: p.fvmma for p in UNIVERSAL}
_ROLES_BY_ID = {p.id: p.roles for p in UNIVERSAL}
_LEGALITY = build_legality(load_compat())


def test_ranked_lineups_are_sorted_best_first_and_drop_infeasible() -> None:
    ranked = ranked_lineups(UNIVERSAL, MODULES, value=UNIVERSAL_VALUE)

    assert len(ranked) == 11  # the universal roster fields every module
    totals = [sum(UNIVERSAL_VALUE[pid] for pid in starts) for _code, starts in ranked]
    assert totals == sorted(totals, reverse=True)


def test_ranked_lineups_over_a_narrow_roster_lists_only_what_is_fieldable() -> None:
    ranked = ranked_lineups(GOLDEN, MODULES, value=GOLDEN_VALUE)

    assert [code for code, _ in ranked] == ["343"]


def test_an_unknown_module_code_is_dropped_not_raised() -> None:
    # a code the platform's `mods` might carry but that is not one of the shipped schemi
    ranked = ranked_lineups(GOLDEN, ["999", "343"], value=GOLDEN_VALUE)

    assert [code for code, _ in ranked] == ["343"]  # '999' silently dropped, no ValueError


def test_lineup_for_module_returns_none_for_an_unknown_code() -> None:
    assert lineup_for_module(GOLDEN, "999", value=GOLDEN_VALUE) is None


@pytest.mark.parametrize("code", MODULES)
def test_the_built_starts_are_confirmed_fieldable_by_legality(code: str) -> None:
    starts = lineup_for_module(UNIVERSAL, code, value=UNIVERSAL_VALUE)

    assert starts is not None and len(starts) == 11
    pool = [MantraPlayer(id=str(pid), roles=_ROLES_BY_ID[pid]) for pid in starts]
    nome = "-".join(code)
    assert nome in fieldable_schemi(pool, _LEGALITY)


# Single-role specialists, three per role: every slot is filled by a player who fits it and
# little else, so a slot sent to the wrong position has nowhere to hide.
_ROLES = ("POR", "DD", "DS", "DC", "B", "E", "M", "C", "T", "W", "A", "PC")
SPECIALISTS = [
    _p(1000 + 10 * r + k, float((37 * (10 * r + k)) % 101), role)
    for r, role in enumerate(_ROLES)
    for k in range(3)
]
SPECIALIST_VALUE = {p.id: p.fvmma for p in SPECIALISTS}


@pytest.mark.parametrize("code", MODULES)
def test_the_built_starts_pass_the_positional_check(code: str) -> None:
    """Beside the set-based check above, which stays: that one cannot see order.

    Legality over a *set* passes an XI the platform refuses, or scores with a malus, once
    the players are sent in the wrong positions. This one judges each starter at the
    platform's own slot i.
    """
    starts = lineup_for_module(SPECIALISTS, code, value=SPECIALIST_VALUE)

    assert starts is not None
    roles = {p.id: p.roles for p in SPECIALISTS}
    assert positional.violations(code, [roles[pid] for pid in starts]) == ()


# --- `place_all_with_malus`: feasibility, the substitution engine's primitive ----------
#
# These five were written against `place_all`, a two-line wrapper that passed `slot_sets`
# as both the natural and the admitted sets and dropped the malus count. It was deleted on
# 2026-09-24 with no caller in `src/`; `substitution.py` calls the matcher below directly.
# The tests were repointed rather than deleted, because their subject was always the
# matcher — and the rows-vs-columns guard in particular has no other coverage: break it
# and `solve_assignment` hangs the run instead of failing it.


def test_place_all_seats_everyone_in_a_slot_his_roles_cover() -> None:
    slots = (frozenset({"POR"}), frozenset({"DC"}), frozenset({"C", "M"}))

    placed = place_all_with_malus([frozenset({"C"}), frozenset({"POR"})], slots, slots)

    assert placed == ([2, 0], 0)


def test_place_all_refuses_when_two_players_want_the_only_slot() -> None:
    slots = (frozenset({"POR"}), frozenset({"DC"}))

    assert place_all_with_malus([frozenset({"POR"}), frozenset({"POR"})], slots, slots) is None


def test_place_all_allows_fewer_players_than_slots() -> None:
    """The man-short case: ten players, eleven slots, one left empty."""
    slots = (frozenset({"POR"}), frozenset({"DC"}), frozenset({"DC"}))

    placed = place_all_with_malus([frozenset({"DC"})], slots, slots)

    assert placed is not None and placed[0] in ([1], [2])


def test_place_all_refuses_more_players_than_slots() -> None:
    """`solve_assignment` needs rows <= columns: with more rows its augmenting-path loop does not
    terminate, so this guard is what stands between a twelfth man and a hung run — not
    merely a wrong answer."""
    slots = (frozenset({"DC"}),)

    assert place_all_with_malus([frozenset({"DC"}), frozenset({"DC"})], slots, slots) is None


def test_place_all_seats_nobody_in_no_slots() -> None:
    slots = (frozenset({"DC"}),)

    assert place_all_with_malus([], slots, slots) == ([], 0)
