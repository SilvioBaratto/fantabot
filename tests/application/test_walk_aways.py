"""The number an operator bids against, in credits — and the unit error it replaces.

**What was on the page.** `reservations` returns
`max(0.0, optimal.objective - objective without him)`, clamped by the remaining budget. That
is an **objective difference**: a sum of `mu` minus `lam * Var`. It was rendered in a column
beside `world.prices`, which is credits. `SPEC.md` §2.A calls it the unit error by name, and
`reservation.py:466-476` says the value is *"advisory only, never the bid decision"*.

Two failures, both measured on the live 529-player pool at `a442499`:

* **The unit.** Calhanoglu: corpus price 71.8, `reservations` 140.5, `lot_ceiling` 95. The
  middle number is not credits and never was.
* **The collapse.** `base - alt` ties whenever a near-substitute exists, because the baseline
  already contains the player being priced. Paz N.: corpus 96.2, `reservations` **0.0**,
  `lot_ceiling` **71**. Five of thirty plan members came back at exactly 0.0, and an operator
  reading `Walk-away 0` beside `Corpus price 96` concludes "let him go".

`lot_reference` + `lot_ceiling` is the pair that already replaced this inside the room
(Task 1.3) — a real re-solve with the lot forced in, priced in credits, against the objective
*without* him. This module is that pair, made available to a caller that prices a whole plan
rather than one lot.

A `lot_ceiling` of 0 is a real answer and keeps its own label: it means the rosa does not
improve by buying him at any price, which is what "a substitute exists" is supposed to say.
"""

from __future__ import annotations

import pytest
from _golden import PINNED_TODAY, load_clearing_sales, load_quotazioni, load_sentiment

from fantabot.application.plan_request import (
    WALK_AWAY_AT_BUDGET,
    WALK_AWAY_HOLD,
    WALK_AWAY_RESOLVED,
    WALK_AWAY_UNPRICED,
    WalkAway,
    walk_aways,
)
from fantabot.domain.asta.state import AstaState, RosterRules


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    """The pinned 548-player Mantra listone — the same world the cost guard measures."""
    from fantabot.application.asta_planner import build_plan_inputs
    from fantabot.domain.asta.prices import Sale, mean_prices

    return build_plan_inputs(
        load_quotazioni(),
        mean_prices(Sale(pid, price) for pid, price in load_clearing_sales()),
        load_sentiment(),
        as_of=PINNED_TODAY,
        tilt_k=0.25,
    )


@pytest.fixture(scope="module")
def priced(world):  # type: ignore[no-untyped-def]
    from fantabot.domain.asta.optimizer import optimize_roster

    state = AstaState(total_budget=500.0)
    rules = RosterRules()
    plan = optimize_roster(
        state, world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, rules=rules, lam=0.0, n_fallbacks=0,
    )
    return plan, walk_aways(state, world, plan.optimal, rules=rules, lam=0.0)


@pytest.fixture(scope="module")
def marginals(world):  # type: ignore[no-untyped-def]
    """What `reservations` said — the number this replaces."""
    from fantabot.domain.asta.reservation import reservations

    _, walkaways = reservations(
        AstaState(total_budget=500.0), world.pool, value=world.value, prices=world.prices,
        teams=world.teams, legality=world.legality, rules=RosterRules(), lam=0.0,
        n_targets=None,
    )
    return walkaways


def test_every_unowned_plan_member_is_priced(priced) -> None:  # type: ignore[no-untyped-def]
    """A plan with unpriced rows is a plan the operator prices by hand."""
    plan, out = priced

    assert set(out) == set(plan.optimal.player_ids)
    assert all(isinstance(w, WalkAway) for w in out.values())


def test_the_number_is_credits_and_never_exceeds_the_cap(priced) -> None:  # type: ignore[no-untyped-def]
    """`reservations` returned objective units clamped by the budget. These are credits,
    bounded by what the roster band can still afford for one lot."""
    _, out = priced

    assert all(isinstance(w.credits, int) for w in out.values())
    assert all(0 <= w.credits <= 500 for w in out.values()), (
        {p: w.credits for p, w in out.items() if not 0 <= w.credits <= 500}
    )


def test_the_collapse_is_gone_where_it_was_measured(priced, marginals, world) -> None:  # type: ignore[no-untyped-def]
    """The regression this module exists for.

    Every plan member `reservations` priced at exactly 0.0 while the market paid real money
    for him must now carry a real ceiling. On the pinned pool the worst case is a corpus
    price of ~96 against a walk-away of 0.
    """
    _, out = priced
    collapsed = [
        pid
        for pid in out
        if marginals.get(pid, 0.0) == 0.0 and world.prices.get(pid, 0.0) >= 10.0
    ]

    assert collapsed, "the fixture no longer reproduces the collapse — re-measure before editing"
    still_zero = [
        (world.names.get(pid, pid), world.prices.get(pid), out[pid].credits)
        for pid in collapsed
        if out[pid].credits == 0
    ]
    assert not still_zero, (
        f"these are worth real money and still price at 0: {still_zero}"
    )


def test_it_disagrees_with_the_marginal_it_replaces(priced, marginals) -> None:  # type: ignore[no-untyped-def]
    """A guard on the guard: if the two agreed everywhere, this module would be a rename."""
    _, out = priced
    differs = [pid for pid in out if abs(out[pid].credits - marginals.get(pid, 0.0)) > 1.0]

    assert len(differs) >= len(out) // 2, (
        "the re-solve agrees with the marginal almost everywhere — one of them is not "
        "running"
    )


def test_a_zero_ceiling_says_a_substitute_exists_rather_than_going_unlabelled(priced) -> None:  # type: ignore[no-untyped-def]
    """0 is a real answer here — "the rosa does not improve by buying him" — and it is the
    one a reader is most likely to misread, so it carries its own provenance."""
    _, out = priced
    zeros = [w for w in out.values() if w.credits == 0]

    assert zeros, "no zero ceilings on the pinned pool — re-measure before editing"
    assert all(w.provenance == WALK_AWAY_HOLD for w in zeros)


def test_the_provenances_are_the_four_named_constants(priced) -> None:  # type: ignore[no-untyped-def]
    """Exact strings, not composed prose — `rules_for_room`'s rule: a provenance an operator
    cannot grep for consistently is one they stop trusting."""
    _, out = priced
    allowed = {WALK_AWAY_RESOLVED, WALK_AWAY_HOLD, WALK_AWAY_AT_BUDGET, WALK_AWAY_UNPRICED}

    assert {w.provenance for w in out.values()} <= allowed


def test_an_owned_player_is_not_priced(world) -> None:  # type: ignore[no-untyped-def]
    """A player already bought is not a purchase to decide."""
    from fantabot.domain.asta.optimizer import optimize_roster

    rules = RosterRules()
    first = optimize_roster(
        AstaState(total_budget=500.0), world.pool, value=world.value, prices=world.prices,
        teams=world.teams, legality=world.legality, rules=rules, lam=0.0, n_fallbacks=0,
    )
    held = first.optimal.player_ids[0]
    state = AstaState(owned=(held,), total_budget=500.0)
    plan = optimize_roster(
        state, world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, rules=rules, lam=0.0, n_fallbacks=0,
    )

    out = walk_aways(state, world, plan.optimal, rules=rules, lam=0.0)

    assert out[held].credits == 0
    assert out[held].provenance == WALK_AWAY_UNPRICED
