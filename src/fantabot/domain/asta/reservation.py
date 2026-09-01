"""Rolling re-optimization and the walk-away (reservation) price. Pure.

``apply_event`` folds a sale into our state; ``reservations`` re-optimizes and prices each
target by how much objective value we lose without him; ``rolling_advisory`` drives the two
over a stream of events, yielding the fresh plan and walk-aways after each sale. The
reservation is in the value signal's own (credit-like) units, capped at the remaining
budget — a v1 stand-in for the shadow-price walk-away ``VOR / (1 + mu)`` that the pacing
task will add.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace

from fantabot.domain.asta.legality import SchemaLegality
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.optimizer import (
    DEFAULT_SAME_TEAM_RHO,
    InfeasibleRoster,
    build_index,
    optimize_roster,
)
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, OptimizationResult, RosterRules
from fantabot.domain.asta.value import ValueModel


def apply_event(state: AstaState, event: AssignmentEvent, *, our_team_id: str) -> AstaState:
    """Fold one sale into our state: ours -> owned + spent; anyone's -> taken."""
    taken = state.taken | {event.player_id}
    if event.buyer_team_id == our_team_id:
        return replace(
            state,
            owned=(*state.owned, event.player_id),
            spent=state.spent + event.price,
            taken=taken,
        )
    return replace(state, taken=taken)


def reservations(
    state: AstaState,
    pool: Sequence[MantraPlayer],
    *,
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: RosterRules = RosterRules(),
    lam: float = 0.0,
    rho: float = DEFAULT_SAME_TEAM_RHO,
    n_targets: int | None = 5,
) -> tuple[OptimizationResult, dict[str, float]]:
    """The current optimal plan, and a credit walk-away for each top target.

    ``walk-away(t) = optimal.objective - objective of the best roster without t``, capped at
    the remaining budget. A target whose removal makes the roster infeasible is essential and
    reserves the whole budget.

    ``n_targets=None`` prices **every** unowned member of the plan. A live room needs that:
    lots are called in arbitrary order, so a plan priced five deep answers "not a target" for
    the other twenty-five, which is indistinguishable from a decision not to chase them.

    **The default stays 5, and moving it would be a mistake.** Pricing the whole plan costs
    one extra roster solve per target — measured on the live pool, 325,538 Python calls at
    five against 1,508,707 at thirty. ``_cycle_calls`` in ``test_asta_cycle_cost.py`` passes
    no ``n_targets``, so the pinned 500,000 ceiling that catches a reverted P10 optimisation
    is measuring this default; re-pointing it at a 3x heavier cycle would retire that
    tripwire silently. The advisory paths keep the cap; only ``asta bid`` asks for all of it.
    """
    # Built once for the whole cycle. Every solve below is over the same pool, the
    # same prices and the same value model, so the per-player facts and the slot
    # eligibility cache are the same each time — computing them per solve was most of
    # what one cycle spent.
    index = build_index(pool, prices, value, rules)

    # n_fallbacks=0: this call's fallbacks are unused — the walk-aways below replace them,
    # so computing them would waste one optimizer build per target on every sale.
    result = optimize_roster(
        state, pool, value=value, prices=prices, teams=teams, legality=legality,
        rules=rules, lam=lam, rho=rho, n_fallbacks=0, index=index,
    )
    base = result.optimal.objective
    owned = set(state.owned)
    targets = sorted(
        (pid for pid in result.optimal.player_ids if pid not in owned),
        key=lambda pid: value.value(pid).mean,
        reverse=True,
    )[:n_targets]

    walkaways: dict[str, float] = {}
    for target in targets:
        without = replace(state, taken=state.taken | {target})
        try:
            alt = optimize_roster(
                without, pool, value=value, prices=prices, teams=teams, legality=legality,
                rules=rules, lam=lam, rho=rho, n_fallbacks=0, index=index,
            ).optimal.objective
            # Clamp to >= 0: the greedy builder is a heuristic, so a roster without the target
            # can occasionally score higher (base - alt < 0). A negative walk-away is meaningless
            # — it just means the target is freely replaceable, i.e. worth nothing extra to chase.
            walkaways[target] = max(0.0, min(state.remaining_budget, base - alt))
        except InfeasibleRoster:
            walkaways[target] = state.remaining_budget
    return result, walkaways


def rolling_advisory(
    state: AstaState,
    pool: Sequence[MantraPlayer],
    events: Iterable[AssignmentEvent],
    *,
    our_team_id: str,
    value_of: Callable[[], ValueModel],
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: RosterRules = RosterRules(),
    lam: float = 0.0,
    rho: float = DEFAULT_SAME_TEAM_RHO,
) -> Iterator[tuple[AstaState, AssignmentEvent, OptimizationResult, dict[str, float]]]:
    """Re-plan after every sale: yield ``(state, event, plan, walkaways)`` per event.

    ``value_of`` is a factory, called once per event, rather than a fixed model. An asta
    runs for hours and the sentiment feed is a live table, so a player ruled out at 21:00
    must stop being a target at 21:01; ``news_sentiment`` holds a session and never a cached
    table for exactly that reason, and taking a ``ValueModel`` here would throw it away one
    layer further up.

    **No caller exercises that yet.** Both of today's callers pass a constant factory, and
    ``asta live`` is right to: ``feed.ledger_events`` materializes the whole ledger in one
    GET before the loop begins, so re-reading between events cannot see anything newer. The
    seam is kept because it is what a polling ``asta live`` will need — re-reading is only
    meaningful once the *ledger* is re-read each cycle — and a constant factory costs
    nothing in the meantime.
    """
    for event in events:
        state = apply_event(state, event, our_team_id=our_team_id)
        result, walkaways = reservations(
            state, pool, value=value_of(), prices=prices, teams=teams, legality=legality,
            rules=rules, lam=lam, rho=rho,
        )
        yield state, event, result, walkaways
