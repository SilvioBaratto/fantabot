"""`candidates` — the outer loop's XIs: Murty's k-best over the sub-aware surrogate.

Four things are pinned here.

The enumeration is **exact**. Murty's k-best is checked against a brute force that a 14-man
roster is small enough to run — a DP over (slot, used-set), which visits ~2.5e5 states where
enumerating assignments would visit 14!/3! = 3.6e9.

The surrogate is SPEC A17(3)'s sub-aware `w[s,i] = p_i*mu_i + (1-p_i)*v_s`, built in **two
solves**: the first fixes who the reserves are, the second prices every (slot, player) pair
against them. A slot-independent surrogate cannot express `v_s`, so the second solve has to
see a matrix, not a vector.

k counts **distinct starter sets** [stats-4], not assignments. With `v_s` in the weight, two
arrangements of the same eleven score differently, so a naive k-best would spend its budget
permuting one set.

And every candidate is **submittable**: natural roles only, and `ok` at every position of the
pinned slot order (A22) — the guard the live LUP009 broke.
"""

from __future__ import annotations

import time
from itertools import pairwise

import pytest

from fantabot.domain.lineup import positional
from fantabot.domain.lineup.build import INELIGIBLE, lineup_for_module, ranked_lineups
from fantabot.domain.lineup.candidates import (
    DEFAULT_K,
    DEFAULT_LAMBDAS,
    assignment_cost,
    build_candidates,
    candidates_for_module,
    first_solve,
    k_best_assignments,
    module_family,
    p_one,
    slot_replacement,
    surrogate_matrix,
)
from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.lineup.schema import slots

MODULES = ("3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442")


def _p(pid: int, *roles: str) -> RosterPlayer:
    return RosterPlayer(id=pid, roles=frozenset(roles), fvmma=0.0)


# -- A 14-man roster that fields 4-3-3, with a real choice at keeper, centre-back and
# -- midfield: 2 x 3 x 4 = 24 distinct starter sets, comfortably more than k.
FOURTEEN = [
    _p(1, "POR"),
    _p(2, "POR"),
    _p(3, "DS"),
    _p(4, "DC"),
    _p(5, "DC"),
    _p(6, "DC"),
    _p(7, "DD"),
    _p(8, "C"),
    _p(9, "C"),
    _p(10, "M"),
    _p(11, "C", "M"),
    _p(12, "W"),
    _p(13, "W"),
    _p(14, "PC"),
]

#: Distinct values throughout, so no two starter sets tie and the k-best order is total.
#: A tie would make "equals brute force" an order-dependent coin flip; the brute force
#: asserts strict separation rather than leaving it to luck.
MU = {p.id: 4.0 + 0.37 * ((7 * p.id) % 17) for p in FOURTEEN}
P = {p.id: 0.45 + 0.03 * ((11 * p.id) % 17) for p in FOURTEEN}
S2 = {p.id: 0.6 + 0.09 * ((13 * p.id) % 17) for p in FOURTEEN}


def _brute_force(cost: list[list[float]], k: int) -> list[tuple[float, frozenset[int]]]:
    """Every distinct column-set's best total, cheapest first — exhaustive by construction.

    `best[mask]` is the cheapest way to fill slots 0..s-1 with exactly the columns in `mask`.
    Which slot took which column is forgotten, which is the whole point: the answer is per
    **set**, and that is what `k_best_assignments` is claimed to enumerate.
    """
    rows, cols = len(cost), len(cost[0])
    best: dict[int, float] = {0: 0.0}
    for slot in range(rows):
        nxt: dict[int, float] = {}
        for mask, total in best.items():
            for col in range(cols):
                bit = 1 << col
                if mask & bit or cost[slot][col] >= INELIGIBLE:
                    continue
                reached = total + cost[slot][col]
                key = mask | bit
                if reached < nxt.get(key, INELIGIBLE):
                    nxt[key] = reached
        best = nxt
    ranked = sorted((total, mask) for mask, total in best.items())
    return [
        (total, frozenset(c for c in range(cols) if mask & (1 << c))) for total, mask in ranked[:k]
    ]


def _cost_433() -> list[list[float]]:
    expected = {pid: P[pid] * MU[pid] for pid in MU}
    starters = first_solve(FOURTEEN, slots("433"), expected=expected)
    assert starters is not None
    reserves = [p.id for p in FOURTEEN if p.id not in starters]
    v = slot_replacement(FOURTEEN, slots("433"), expected=expected, reserves=reserves)
    weights = surrogate_matrix(
        FOURTEEN, slots("433"), mu=MU, p=P, sigma2=S2, lam=0.0, replacement=v
    )
    return assignment_cost(FOURTEEN, slots("433"), weights)


# -- k-best is exact ---------------------------------------------------------------------


def test_k_best_equals_brute_force_on_a_fourteen_man_roster() -> None:
    cost = _cost_433()

    found = k_best_assignments(cost, k=DEFAULT_K, node_budget=10_000)
    expected = _brute_force(cost, DEFAULT_K)

    assert len(found) == DEFAULT_K
    assert [round(total, 9) for total, _ in found] == [round(total, 9) for total, _ in expected]
    assert [frozenset(a) for _, a in found] == [cols for _, cols in expected]


def test_the_brute_force_separates_every_set_so_the_order_is_not_a_tie_break() -> None:
    """A tie would make the comparison above order-dependent; assert it cannot happen.

    24 is the roster's own count: 2 keepers x 3 ways to pick 2 of 3 centre-backs x 4 ways to
    fill C/M/(C|M) from four midfielders. It is asserted, not assumed, because a fixture that
    quietly lost its choices would make "equals brute force" a much weaker claim.
    """
    totals = [total for total, _ in _brute_force(_cost_433(), 999)]

    assert len(totals) == 24
    assert all(later - earlier > 1e-9 for earlier, later in pairwise(totals))


def test_k_counts_distinct_starter_sets_not_assignments() -> None:
    cost = _cost_433()

    found = k_best_assignments(cost, k=DEFAULT_K, node_budget=10_000)

    assert len({frozenset(a) for _, a in found}) == DEFAULT_K


def test_every_returned_assignment_is_eligible_and_costs_what_it_claims() -> None:
    cost = _cost_433()

    for total, assignment in k_best_assignments(cost, k=DEFAULT_K, node_budget=10_000):
        assert len(set(assignment)) == len(assignment)
        assert all(cost[s][c] < INELIGIBLE for s, c in enumerate(assignment))
        assert total == pytest.approx(sum(cost[s][c] for s, c in enumerate(assignment)))


def test_an_infeasible_matrix_is_detected_by_cost_and_yields_nothing() -> None:
    """Infeasibility never raises: `solve_assignment` always returns a full assignment, and
    an ineligible edge is only visible in the total."""
    cost = [[INELIGIBLE, 1.0], [INELIGIBLE, 2.0]]

    assert k_best_assignments(cost, k=5, node_budget=100) == []


def test_an_infeasible_branch_is_refused_however_negative_its_real_part_is() -> None:
    """The surrogate is a score, so every eligible edge costs `-w` and an eleven's real part
    is deeply negative. A single ineligible edge then totals `INELIGIBLE - 70`, which is
    *below* the threshold — so reading infeasibility off the **total** accepts a player in a
    slot his roles do not cover, and no magnitude of `INELIGIBLE` repairs it. Measured
    2026-09-22: this matrix came back as a candidate.
    """
    cost = [
        [-500.0, -500.0, INELIGIBLE],
        [-500.0, -500.0, INELIGIBLE],
        [INELIGIBLE, INELIGIBLE, INELIGIBLE],  # no column is eligible for this row
    ]

    assert k_best_assignments(cost, k=3, node_budget=100) == []


def test_ineligible_edges_must_be_dominated_or_a_fieldable_matrix_reads_as_infeasible() -> None:
    """The other half of the same constant. Row 0 fits only column 0, and row 1 would rather
    have column 0 by 99. `INELIGIBLE` has to outweigh that preference, or the matcher takes
    the cheaper *ineligible* pairing and the one real assignment is never found.
    """
    cost = [[-1.0, INELIGIBLE], [-100.0, -1.0]]

    found = k_best_assignments(cost, k=2, node_budget=100)

    assert [a for _, a in found] == [(0, 1)]
    assert found[0][0] == pytest.approx(-2.0)


def test_more_rows_than_columns_is_refused_rather_than_hung() -> None:
    """`solve_assignment` assumes rows <= columns and does not terminate otherwise — the
    failure `place_all`'s own guard was measured hanging a whole run on 2026-09-22. Reached
    through `module_family` the case cannot arise, but this is a public entry point.
    """
    assert k_best_assignments([[-1.0], [-2.0]], k=1, node_budget=10) == []


def test_fewer_sets_than_k_returns_what_exists() -> None:
    cost = [[-1.0, -2.0], [-3.0, -4.0]]

    found = k_best_assignments(cost, k=9, node_budget=100)

    assert [frozenset(a) for _, a in found] == [frozenset({0, 1})]


def test_the_node_budget_bounds_the_search_and_keeps_what_it_found() -> None:
    cost = _cost_433()

    bounded = k_best_assignments(cost, k=DEFAULT_K, node_budget=12)
    full = k_best_assignments(cost, k=DEFAULT_K, node_budget=10_000)

    assert 0 < len(bounded) < len(full)
    assert bounded == full[: len(bounded)]  # a prefix: what it found is still best-first


# -- the surrogate, in two solves --------------------------------------------------------


def test_the_first_solve_is_the_expected_points_xi_and_fixes_the_reserves() -> None:
    expected = {pid: P[pid] * MU[pid] for pid in MU}

    starters = first_solve(FOURTEEN, slots("433"), expected=expected)

    assert starters is not None
    assert len(starters) == 11
    assert set(starters) == set(lineup_for_module(FOURTEEN, "433", value=expected) or ())


def test_the_first_solve_refuses_a_module_the_roster_cannot_field() -> None:
    assert first_solve(FOURTEEN[:11], slots("442"), expected=MU) is None


def test_v_s_is_the_best_reserve_covering_that_slot_and_zero_where_none_does() -> None:
    expected = {1: 9.0, 2: 3.0, 3: 5.0, 4: 1.0}
    roster = [_p(1, "POR"), _p(2, "DC"), _p(3, "DC"), _p(4, "W")]
    slot_sets = (
        frozenset({"POR"}),
        frozenset({"DC"}),
        frozenset({"A", "PC"}),
        frozenset({"A", "W"}),
    )

    v = slot_replacement(roster, slot_sets, expected=expected, reserves=[2, 3, 4])

    # The keeper is the best man on the roster and contributes nothing: he is not a reserve.
    # Nobody on the bench covers A/PC, so an absent striker there is worth 0 and not the 5.0
    # the bench's best man is worth at a slot he cannot take — the case a single replacement
    # level gets wrong.
    assert v == (0.0, 5.0, 0.0, 1.0)


def test_the_surrogate_is_p_mu_plus_one_minus_p_times_v_s() -> None:
    roster = [_p(1, "DC"), _p(2, "DC")]
    slot_sets = (frozenset({"DC"}), frozenset({"DC"}))
    mu = {1: 8.0, 2: 6.0}
    p = {1: 0.5, 2: 1.0}

    weights = surrogate_matrix(
        roster, slot_sets, mu=mu, p=p, sigma2={1: 2.0, 2: 3.0}, lam=0.0, replacement=(4.0, 1.0)
    )

    assert weights == [[0.5 * 8.0 + 0.5 * 4.0, 1.0 * 6.0], [0.5 * 8.0 + 0.5 * 1.0, 1.0 * 6.0]]


def test_lambda_tilts_by_the_predictive_variance_and_signs_as_declared() -> None:
    roster = [_p(1, "DC")]
    slot_sets = (frozenset({"DC"}),)
    args = {"mu": {1: 6.0}, "p": {1: 1.0}, "sigma2": {1: 2.0}, "replacement": (0.0,)}

    flat = surrogate_matrix(roster, slot_sets, lam=0.0, **args)  # type: ignore[arg-type]
    seeking = surrogate_matrix(roster, slot_sets, lam=0.25, **args)  # type: ignore[arg-type]
    avoiding = surrogate_matrix(roster, slot_sets, lam=-0.25, **args)  # type: ignore[arg-type]

    assert flat[0][0] == 6.0
    assert seeking[0][0] == 6.0 + 0.25 * 2.0
    assert avoiding[0][0] == 6.0 - 0.25 * 2.0


def test_v_s_moves_the_second_solve_away_from_the_first() -> None:
    """The two solves are not a formality: with a strong reserve behind one slot only, the
    sub-aware matrix fields a different eleven from the expected-points one it was built on.

    A risky man is worth more where the fallback is good, which is exactly what `v_s` says
    and what a slot-independent `p*mu` cannot.
    """
    roster = [
        _p(1, "POR"),
        _p(2, "DC"),
        _p(3, "W"),  # risky, and the W slot has a strong reserve behind it
        _p(4, "PC"),  # safe, and nothing covers the PC slot but him
        _p(5, "W"),  # the reserve that makes v_s large at the W slot
    ]
    slot_sets = (frozenset({"POR"}), frozenset({"DC"}), frozenset({"W", "PC"}))
    mu = {1: 6.0, 2: 6.0, 3: 10.0, 4: 6.4, 5: 6.0}
    p = {1: 1.0, 2: 1.0, 3: 0.5, 4: 1.0, 5: 1.0}
    expected = {pid: p[pid] * mu[pid] for pid in mu}

    starters = first_solve(roster, slot_sets, expected=expected)
    assert starters == (1, 2, 4)  # p*mu alone benches the risky winger: 5.0 < 6.4

    reserves = [pid for pid in mu if pid not in starters]
    v = slot_replacement(roster, slot_sets, expected=expected, reserves=reserves)
    weights = surrogate_matrix(
        roster, slot_sets, mu=mu, p=p, sigma2={pid: 1.0 for pid in mu}, lam=0.0, replacement=v
    )
    cost = assignment_cost(roster, slot_sets, weights)
    best = k_best_assignments(cost, k=1, node_budget=100)

    assert v[2] == 6.0  # player 5 covers the third slot; he is the fallback there
    assert [roster[c].id for c in best[0][1]] == [1, 2, 3]  # 5.0 + 0.5*6.0 = 8.0 > 6.4


# -- the families ------------------------------------------------------------------------


def test_the_p_one_family_contains_the_baseline_xi_and_leads_with_it() -> None:
    baseline = lineup_for_module(FOURTEEN, "433", value=MU)
    assert baseline is not None

    family = module_family(
        FOURTEEN, "433", mu=MU, p=p_one(MU), sigma2=S2, lam=0.0, family="p1", k=DEFAULT_K
    )

    assert family[0].starter_set == frozenset(baseline)
    assert frozenset(baseline) in {c.starter_set for c in family}


def test_p_one_is_every_projected_player_at_certainty() -> None:
    assert p_one(MU) == dict.fromkeys(MU, 1.0)


def test_the_p_one_family_differs_from_the_sub_aware_one() -> None:
    """Otherwise it is not a family, it is a duplicate: availability has to change the XI."""
    sub_aware = module_family(
        FOURTEEN, "433", mu=MU, p=P, sigma2=S2, lam=0.0, family="tilt+0", k=DEFAULT_K
    )
    certain = module_family(
        FOURTEEN, "433", mu=MU, p=p_one(MU), sigma2=S2, lam=0.0, family="p1", k=DEFAULT_K
    )

    assert sub_aware[0].starter_set != certain[0].starter_set


def test_a_family_labels_and_prices_its_candidates() -> None:
    family = module_family(
        FOURTEEN, "433", mu=MU, p=P, sigma2=S2, lam=0.125, family="tilt+0.125", k=3
    )

    assert [c.family for c in family] == ["tilt+0.125"] * 3
    assert [c.module for c in family] == ["433"] * 3
    assert all(len(c.starts) == 11 for c in family)
    surrogates = [c.surrogate for c in family]
    assert surrogates == sorted(surrogates, reverse=True)


def test_an_unfieldable_module_yields_no_candidates_rather_than_raising() -> None:
    assert module_family(FOURTEEN, "352", mu=MU, p=P, sigma2=S2, lam=0.0, family="f", k=5) == []
    assert candidates_for_module(FOURTEEN, "352", mu=MU, p=P, sigma2=S2) == []


def test_an_unknown_module_code_is_dropped_like_the_builder_drops_it() -> None:
    assert candidates_for_module(FOURTEEN, "999", mu=MU, p=P, sigma2=S2) == []


def test_the_union_is_deduplicated_on_module_and_starter_set() -> None:
    union = candidates_for_module(FOURTEEN, "433", mu=MU, p=P, sigma2=S2)

    keys = [(c.module, c.starter_set) for c in union]
    assert len(keys) == len(set(keys))
    assert len(union) > DEFAULT_K  # the families genuinely disagree


def test_the_union_is_discovery_ordered_over_the_grid_then_p_one() -> None:
    union = candidates_for_module(FOURTEEN, "433", mu=MU, p=P, sigma2=S2)
    families = [c.family for c in union]

    assert families[0] == f"tilt{DEFAULT_LAMBDAS[0]:+g}"
    assert families == sorted(
        families, key=lambda label: [f"tilt{lam:+g}" for lam in DEFAULT_LAMBDAS].index(label)
        if label != "p1"
        else len(DEFAULT_LAMBDAS)
    )
    assert frozenset(lineup_for_module(FOURTEEN, "433", value=MU) or ()) in {
        c.starter_set for c in union
    }


def test_p_one_earns_its_place_in_the_union() -> None:
    """The label a set keeps is its **first** finder, so a full union says nothing about who
    would have found it alone. Squeeze k to 1 and the question is answerable: on this roster
    every tilt agrees on one eleven, and the baseline XI reaches the shortlist only because
    the p = 1 family put it there.
    """
    union = candidates_for_module(FOURTEEN, "433", mu=MU, p=P, sigma2=S2, k=1)
    baseline = frozenset(lineup_for_module(FOURTEEN, "433", value=MU) or ())

    by_family = {c.family: c.starter_set for c in union}
    assert by_family["p1"] == baseline
    assert baseline not in {s for f, s in by_family.items() if f != "p1"}


def test_the_same_inputs_give_the_same_candidates() -> None:
    once = candidates_for_module(FOURTEEN, "433", mu=MU, p=P, sigma2=S2)
    twice = candidates_for_module(FOURTEEN, "433", mu=MU, p=P, sigma2=S2)

    assert once == twice


# -- a full roster: legality and the budget ----------------------------------------------

BIG = [
    *(_p(100 + i, "POR") for i in range(3)),
    *(_p(200 + i, "DC") for i in range(4)),
    *(_p(210 + i, "DD") for i in range(2)),
    *(_p(220 + i, "DS") for i in range(2)),
    _p(230, "B"),
    *(_p(300 + i, "E") for i in range(3)),
    *(_p(310 + i, "M") for i in range(3)),
    *(_p(320 + i, "C") for i in range(3)),
    *(_p(400 + i, "W") for i in range(3)),
    *(_p(410 + i, "T") for i in range(2)),
    *(_p(500 + i, "A") for i in range(2)),
    *(_p(510 + i, "PC") for i in range(2)),
]
BIG_MU = {p.id: 3.0 + 0.23 * ((7 * p.id) % 29) for p in BIG}
BIG_P = {p.id: 0.40 + 0.02 * ((11 * p.id) % 29) for p in BIG}
BIG_S2 = {p.id: 0.5 + 0.07 * ((13 * p.id) % 29) for p in BIG}


def test_the_big_roster_fields_all_eleven_modules() -> None:
    """Otherwise the budget and legality tests below grade a smaller problem than they say."""
    assert len(BIG) == 30
    assert {code for code, _ in ranked_lineups(BIG, MODULES, value=BIG_MU)} == set(MODULES)


def test_every_candidate_is_ok_at_every_position_of_the_pinned_order() -> None:
    """A22: one pinned order per module, no alternatives. Natural roles alone were never the
    guarantee — the platform judges `starts[i]` at its own slot i."""
    by_id = {p.id: p for p in BIG}

    candidates = build_candidates(BIG, MODULES, mu=BIG_MU, p=BIG_P, sigma2=BIG_S2)

    assert candidates
    for candidate in candidates:
        roles = [by_id[pid].roles for pid in candidate.starts]
        assert positional.refusal(candidate.module, roles) == ""


def test_every_candidate_starts_eleven_distinct_owned_players_keeper_first() -> None:
    by_id = {p.id: p for p in BIG}

    for candidate in build_candidates(BIG, MODULES, mu=BIG_MU, p=BIG_S2, sigma2=BIG_S2):
        assert len(candidate.starts) == 11
        assert len(set(candidate.starts)) == 11
        assert set(candidate.starts) <= set(by_id)
        assert "POR" in by_id[candidate.starts[0]].roles


def test_every_module_contributes_and_the_union_is_deduplicated() -> None:
    candidates = build_candidates(BIG, MODULES, mu=BIG_MU, p=BIG_P, sigma2=BIG_S2)

    keys = [(c.module, c.starter_set) for c in candidates]
    assert len(keys) == len(set(keys))
    assert {c.module for c in candidates} == set(MODULES)


@pytest.mark.parametrize("k", [DEFAULT_K])
def test_eleven_modules_five_lambdas_and_k_ten_run_inside_a_second(k: int) -> None:
    assert len(DEFAULT_LAMBDAS) == 5

    started = time.perf_counter()
    candidates = build_candidates(BIG, MODULES, mu=BIG_MU, p=BIG_P, sigma2=BIG_S2, k=k)
    elapsed = time.perf_counter() - started

    assert len(candidates) >= 11 * k
    assert elapsed < 1.0, f"{elapsed:.3f}s for 11 modules x 5 lambdas x k={k}"
