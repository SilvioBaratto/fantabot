"""Rolling re-optimization and the walk-away (reservation) price. Pure.

``apply_event`` folds a sale into our state; ``reservations`` re-optimizes and prices each
target by how much objective value we lose without him; ``rolling_advisory`` drives the two
over a stream of events, yielding the fresh plan and walk-aways after each sale. The
reservation is in the value signal's own (credit-like) units, capped at the remaining
budget — a v1 stand-in for the shadow-price walk-away ``VOR / (1 + mu)`` that the pacing
task will add.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace

from fantabot.domain.asta.legality import SchemaLegality
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.opponents import MIN_BID
from fantabot.domain.asta.optimizer import (
    DEFAULT_SAME_TEAM_RHO,
    Candidate,
    CompositionRules,
    InfeasibleRoster,
    build_index,
    optimize_roster,
    planning_cost,
)
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, OptimizationResult, RosterRules
from fantabot.domain.asta.value import ValueModel
from fantabot.domain.classic.roles import ClassicPlayer
from fantabot.domain.classic.state import ClassicRosterRules


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


#: What fraction of a lot's observed clearing price makes a player the plan *did not* pick
#: worth taking anyway.
#:
#: **The plan rejects a player at his book price and says nothing about him at a third of
#: it.** Measured 2026-09-01: Malen (5585) is out of the 30-man plan because 189 of 398
#: credits on 1 of 27 slots drops the objective 2112.1 -> 2041.4. That is a loss of 70.7 fvm
#: for 189 credits committed, against a plan productivity of 2112/500 = 4.22 fvm per credit
#: — so forcing him in at book destroys about 70.7/4.22 = 17 credits of plan, and he is
#: *break-even near 0.91 x book*. 0.60 is deliberately well inside that: the one point we can
#: compute is the shallowest rejection in the pool (he is out by a hair), and a player the
#: optimizer rejects sharply breaks even far lower. Below 0.60 the discount is large enough
#: that no per-player re-solve is needed to believe it, which is the whole point — the loop
#: cannot afford one.
#:
#: It is also strictly below 1.0, so the pre-gate's own cap sits under a lot's full book price
#: before `lot_ceiling` ever re-solves it — `opportunistic_walkaway`'s ``share`` and ``max_cap``
#: terms are the guards that keep a bargain from starving what the plan itself needs.
BARGAIN_BETA = 0.6

#: Below this book price a "bargain" is noise. At 20 credits a 40% discount saves 8, which is
#: inside the rounding on a single planned lot; and ``planning_cost`` returns 1 for the 154
#: pool players with no observed sale, so without a floor every unpriced riserva would look
#: like a bargain at 1 credit. The plan already buys the cheap end deliberately.
BARGAIN_MIN_BOOK = 20


def opportunistic_walkaway(
    player: Candidate,
    *,
    owned_players: Sequence[Candidate],
    prices: Mapping[str, float],
    plan: Sequence[str],
    owned: Sequence[str],
    legality: dict[str, SchemaLegality],
    rules: CompositionRules,
    max_cap: int,
    beta: float = BARGAIN_BETA,
    min_book: int = BARGAIN_MIN_BOOK,
) -> int | None:
    """The **pre-gate** for a lot the plan did not name: a cap to test, or ``None`` to hold.

    Pure, and no re-solve. ``reservations`` prices only the plan's own members, so every
    other lot came back ``walk_away=None`` and the room held at any price — a player worth
    189 sitting at 30 was indistinguishable on screen from one we had decided to let go.

    **What it returns is not a price to pay.** It is the ceiling a purchase would have to
    beat the plan *under*, handed to ``lot_ceiling``, which re-solves and either confirms
    it, lowers it, or refuses outright — so treating this number as the answer buys a rosa the
    objective says is worse than the plan. Measured on the live pool on 2026-09-01 from a
    3-owned/408-credit state: this gate admitted **51 of 496** unplanned lots, and the
    re-solve then priced **2** of those 51 above zero and refused the other 49.

    Everything here is dict lookups and one bipartite match, deliberately: ``asta room``
    already spends 1 + |plan| roster solves per cycle, so what this filters out is what the
    per-lot solve never has to price. It refused 445 of 496 for free.

    Four gates, and the cap is the fifth:

    * **discount** — ``beta * planning_cost``, see ``BARGAIN_BETA``;
    * **materiality** — ``book >= min_book``, see ``BARGAIN_MIN_BOOK``;
    * **share** — never more than the plan's own dearest outstanding target. That is a
      single-lot price the plan has already shown it can absorb, and it falls on its own as
      the evening spends the budget down, so the bargain tightens exactly when credits get
      scarce. Together with ``max_cap`` this is what stops a bargain starving the plan;
    * **band and slot** — ``max_bid`` reserves *credits* and checks no role at all. Buying a
      third POR, or a player whose roles no schema has a slot for, makes the rosa unfieldable
      and ``docs/fantalab/01:142`` says the server accepts the raise anyway.
    """
    if beta <= 0.0:
        return None
    held = set(owned)
    if player.id in held or player.id in set(plan):
        return None

    book = planning_cost(player.id, prices)
    if book < min_book:
        return None

    outstanding = [pid for pid in plan if pid not in held]
    if not outstanding:
        return None
    share = max(planning_cost(pid, prices) for pid in outstanding)

    ceiling = min(int(beta * book), share, max_cap)
    if ceiling < MIN_BID:
        return None

    # Classic (`sroles=1`): the only band check is the per-role ceiling — a third keeper, or a
    # ninth defender, makes the rosa unfieldable. Every P/D/C/A role is fielded by some
    # formation, so there is no unfillable-slot case to test (the Mantra one below).
    if isinstance(rules, ClassicRosterRules):
        assert isinstance(player, ClassicPlayer)  # dispatch pairs Classic rules with a Classic pool
        have = sum(1 for p in owned_players if isinstance(p, ClassicPlayer) and p.role == player.role)
        if have >= rules.max_of(player.role):
            return None
        return ceiling

    assert isinstance(player, MantraPlayer)
    keepers = sum(
        1 for p in owned_players if isinstance(p, MantraPlayer) and p.roles <= rules.goalkeeper_roles
    )
    if player.roles <= rules.goalkeeper_roles:
        if keepers >= rules.max_goalkeepers():
            return None
    elif len(owned_players) - keepers >= rules.max_movement():
        return None

    # NOT `fieldable_schemi`: `can_field` matches a *full* XI, so a partial rosa fields
    # nothing and the gate would refuse every bargain all evening. The question here is the
    # weaker one it is safe to ask mid-auction — is there any slot in any schema that would
    # ever take him — which is what catches a role no schema fields at submission.
    if not any(
        not slot.submission.isdisjoint(player.roles)
        for schema in legality.values()
        for slot in schema.slots
    ):
        return None
    return ceiling


#: How far the re-solved objective must beat the plan's before a price is believed.
#:
#: **Relative to the player's own value, not the whole plan's.** It was not always this way,
#: and the earlier version is worth recording because it broke silently rather than loudly.
#: The original formula was ``max(ABS, REL * abs(baseline))`` — the *whole-plan* objective,
#: which sits in the thousands. Generalizing this function from "a lot the plan did not name"
#: to "any lot, including one already in the plan" surfaces why that scale is wrong: forcing
#: an *already-planned* member back in at a nearby price moves the objective by tens of
#: points, never enough to clear a margin computed off a thousands-scale total. Measured
#: directly on the shipped golden pool (``tests/golden``, 2026-08-28, ``lam=0.3``, empty
#: state, plan objective 2251.8): every one of five already-planned defenders/midfielders
#: (Bremer, Akanji, Rrahmani, Svilar, Wesley) returned a ceiling of **exactly 0** under the
#: old formula — not because none of them were worth keeping, but because
#: ``0.15 * 2251.8 = 337.8`` dwarfs any single-player price change. A margin that can never be
#: cleared is not a noise filter, it is a silent "always refuse".
#:
#: Scaling to the player's own mean value (``value.value(player_id).mean``) fixes this without
#: discarding the coefficient: the same 0.15 now measures against the size of *this* decision
#: rather than the size of the whole rosa. Re-measured on the same pool and state: Malen
#: (book 189, µ 638.2, outside the plan) → ceiling **129**; Bremer (book 31.4, µ 72.6) →
#: **27**; Akanji (book 23.0, µ 68.5) → **19**; Rrahmani (book 18.7, µ 70.1) → **15**; Svilar
#: (book 42.2, µ 111.9) → **38**; Wesley (book 37.2, µ 110.5) → **33**. Every one is a real,
#: non-zero number, and the already-planned five sit a few credits *under* book rather than
#: over it — which is the right shape for "still worth it", since paying a premium over book
#: to keep someone we could already afford is a decision for ``--ceiling-alpha``, not this
#: function.
#:
#: **What is inherited, not re-verified.** The original null-move measurement this file cited
#: (1,754 moves across 90 randomised states, signed jitter -9.85%..+9.38%) was measured
#: against the *old*, baseline-relative formula and no committed script reproduces it — see
#: `SPEC.md` §2.F. It does not directly justify 0.15 against µ either. 0.15 is kept as the
#: conservative default it already was, not re-derived; re-measuring the null-move jitter
#: scaled to µ, with a committed script, is open work (`SPEC.md` §9).
#:
#: ``ABS`` keeps a small fixture from being swamped by a percentage of almost nothing.
CEILING_MARGIN_REL = 0.15
CEILING_MARGIN_ABS = 1.0

#: What share of the **starting** budget one evening may spend on lots the plan never named.
#:
#: Each bargain is approved against the plan on its own, and "better than the plan" is not a
#: transitive property: two lots that each improve the rosa can, bought together, leave a
#: purse that buys neither of the players the second re-solve assumed we would still afford.
#: Nothing else in the loop notices — ``max_bid`` reserves credits per remaining slot and is
#: happy to see them go on anything, and the server enforces no cap at all
#: (``docs/fantalab/01:142``).
#:
#: 0.10 of 500 is 50 credits: enough for the one or two genuine mark-downs an evening throws
#: up, and small enough that spending all of it cannot reshape the plan. It is a *conservative*
#: default on purpose — the opportunistic path is off by default (``--bargain-beta 0``), so the
#: operator who turns it on is opting into an unproven behaviour, and this is the blast radius.
BARGAIN_BUDGET_SHARE = 0.10


def bargain_allowance(
    total_budget: float,
    spent_on_bargains: float,
    *,
    share: float = BARGAIN_BUDGET_SHARE,
) -> int:
    """Credits still available for opportunistic buys this evening. Pure.

    Against the **starting** budget, not the remaining one: a cap that floats with what is
    left rises again every time the plan spends, so an evening could keep re-earning its own
    bargain allowance and the aggregate limit would not exist. The share of a fixed number is
    a fixed number.

    Truncating down (``int``) is the safe direction, and it matches ``lot_ceiling``'s own
    convention, which treats a sub-``MIN_BID`` number as "do not chase".
    """
    return max(0, int(share * total_budget) - int(spent_on_bargains))


def safe_ceiling(passes: Callable[[int], bool], *, lo: int, hi: int) -> int:
    """The largest ``p`` in ``[lo, hi]`` such that ``passes(q)`` holds at **every** ``q <= p``.

    A linear scan, and the uniformity is the whole point. The obvious implementation is a
    bisection, which is correct only if ``passes`` is a step function — true once and then
    false for ever. **It is not.**

    Measured on the live pool on 2026-09-01 (529 players, 418 priced, a 3-owned/408-credit
    state), sweeping ``f(p)`` — the plan objective with one player forced in at ``p`` — one
    credit at a time over the 51 lots the pre-gate admitted: ``f`` **rose at 343 of 1,265
    adjacent credit steps**, the largest single-credit rise being **+102.8** objective points.
    ``f`` cannot really rise in ``p``: a credit spent on this lot is a credit the rest of the
    rosa does not have. Every one of those rises is the greedy builder re-ordering itself
    under a different purse.

    The consequence is not academic. On that same state, 2 of the 51 lots had a price that
    **fails** the rule sitting strictly below one that passes. A bisection lands on the upper
    one, returns it as the ceiling, and ``decide_bid`` then ramps ``current + 1`` straight
    through the failing price in between — so the room can win at a price the criterion
    rejects, and nothing on screen says so.

    Scanning up from ``lo`` and stopping at the first failure is what makes the returned
    number mean what the caller reads it as: *no winnable price under this ceiling violates
    the criterion*. It is a strictly weaker, strictly honest answer — never above the
    bisection's, sometimes below it.

    **It is also cheaper than it looks**, because the scan stops at the first failure rather
    than walking to ``hi`` — and under the shipped margin the first probe is where almost
    every lot stops. Measured on the live pool on 2026-09-01 in the exact shipped
    configuration (``CEILING_MARGIN_REL = 0.15``, ``BARGAIN_BUDGET_SHARE = 0.10``), over the
    51 lots the pre-gate admits: **median 3.3 ms a lot, p95 3.7 ms, worst 110 ms**, and 0.34 s
    to price the entire pool. The room prices only the one lot on the block, once per state,
    against a 2 s poll — so the worst lot in the pool costs a twentieth of one cycle.

    The unbounded worst case is ``hard_cap`` solves at ~2.1 ms each, and ``hard_cap`` is
    itself bounded by the pre-gate's share term and by the evening's bargain allowance (50
    credits at the shipped share of a 500-credit purse). That is ~0.1 s, which is what the
    110 ms above is.

    Returns 0 when ``hi < lo`` or when even ``lo`` fails, which is the "do not chase"
    convention ``decide_bid`` already applies to a walk-away below ``MIN_BID`` elsewhere.
    """
    ceiling = 0
    for price in range(lo, hi + 1):
        if not passes(price):
            break
        ceiling = price
    return ceiling


def lot_reference(
    state: AstaState,
    pool: Sequence[Candidate],
    *,
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: CompositionRules = RosterRules(),
    lam: float = 0.0,
    rho: float = DEFAULT_SAME_TEAM_RHO,
    baseline: float,
    player_id: str,
    plan: Collection[str],
) -> float | None:
    """The objective ``lot_ceiling`` must beat to justify buying ``player_id``. Pure.

    ``baseline`` when he is not a member of ``plan`` — the plan's own optimum already
    excludes him, so ``baseline`` already *is* "the objective without him" and there is
    nothing further to solve. Re-solved with him forced **out**, otherwise: forcing an
    already-planned member back in at his own book price changes nothing else about the
    roster (nobody else moves, no budget frees up), so ``f(p)`` only ever *ties*
    ``baseline`` and can never clear ``lot_ceiling``'s margin — comparing a plan member
    against a total that already includes him at his book price answers a different
    question than the one being asked. Measured on the golden pool (2026-08-28, ``lam=0.3``,
    empty state): 17 of the 30 plan members return a ceiling of 0 against ``baseline`` this
    way, every one already priced within a credit of the floor with no budget to free by
    paying less — including one with ``mu=61.0``, not a marginal player by any reading.
    Re-solving against the objective with each one excluded instead fixes 14 of those 17
    (real numbers, e.g. 62, 21, 20, 10, 8 for the five costliest); the remaining 3 stay at 0
    even against their own ``alt`` (``baseline - alt`` of 0.14, 1.08, 0.32 against a margin of
    ~3), and correctly so — a near-identical substitute genuinely exists for them, which is
    a real "he is fungible" answer and not the bug this function fixes.

    Returns ``None`` when removing him leaves no completable roster at all — the same
    "essential" case ``reservations()`` already special-cases (``except InfeasibleRoster:
    walkaways[target] = state.remaining_budget``). This function only answers whether an
    alternative exists; the caller decides what "reserve the budget" means for its own
    ``hard_cap``.
    """
    if player_id not in plan:
        return baseline
    without = replace(state, taken=state.taken | {player_id})
    try:
        return optimize_roster(
            without, pool, value=value, prices=prices, teams=teams, legality=legality,
            rules=rules, lam=lam, rho=rho, n_fallbacks=0,
        ).optimal.objective
    except InfeasibleRoster:
        return None


def lot_ceiling(
    state: AstaState,
    pool: Sequence[Candidate],
    *,
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: CompositionRules = RosterRules(),
    lam: float = 0.0,
    rho: float = DEFAULT_SAME_TEAM_RHO,
    baseline: float,
    player_id: str,
    hard_cap: int,
    margin_rel: float = CEILING_MARGIN_REL,
    margin_abs: float = CEILING_MARGIN_ABS,
) -> int:
    """The most we would pay for the lot on the block. Pure, and it re-solves.

    ``baseline`` is the objective to beat, not necessarily ``reservations()``'s own plan
    objective — see ``lot_reference``, which computes the right one for a plan member (the
    objective with him excluded) versus an unplanned lot (the plan objective itself,
    unchanged). This function does not care which; it only applies the criterion.

    Says nothing about whether ``player_id`` is a member of the plan — this is deliberate.
    ``reservations()`` prices only its own optimal roster, so a lot outside it holds at any
    price and a lot inside it prices off a marginal (``objective with him`` minus ``without
    him``) that collapses to 0 whenever a near-substitute exists (`SPEC.md` §2.A). Both are the
    same bug: neither asks the question that actually decides a purchase, which has one
    answer regardless of where the lot started::

        f(p) = objective of the best rosa we can still build having bought him at p
        take him at p  <=>  f(p) >= baseline + margin

    ``baseline`` is ``reservations``' own ``plan.optimal.objective``, computed one call
    earlier in the same cycle from the same state — so the two rosters differ in exactly one
    thing, whether he is in them at price ``p``. ``margin`` is the greedy builder's own noise
    band, scaled to *this player's* value (``value.value(player_id).mean``), not the whole
    plan's — see ``CEILING_MARGIN_REL`` for why: the whole-plan scale returns a ceiling of
    exactly 0 for every already-planned member, because a single player's price change never
    moves a thousands-scale total by enough to matter.

    **The ceiling is the largest price at which the rule holds and below which it also
    holds**, scanned up from ``MIN_BID`` by ``safe_ceiling``. It was a bisection, and a
    bisection is wrong here: it assumes ``f`` is non-increasing in ``p``, and on the live pool
    ``f`` rises at 343 of 1,265 adjacent credit steps — up to +102.8 in one credit — because
    the builder is greedy and re-orders itself under a different purse. A bisection can
    therefore return a ceiling *above* a price that fails, and ``decide_bid`` ramps
    ``current + 1`` right through it. See ``safe_ceiling`` for the measurement.

    **Returns 0 for "hold", and 0 is the safe value**: ``decide_bid`` refuses at every price
    when the walk-away is below ``MIN_BID`` — the same convention the walk-away floor this
    function replaced (Task 1.3) used to clamp against.

    **The answer does not mention the lot's current price**, only ``state``. That is
    deliberate and not an accident of the arithmetic: it is what lets the caller memoize one
    number for the 20-60 s a lot spends on the block instead of re-solving per poll, and it is
    what makes a lot bid past its ceiling a *named* pass — ``decide_bid`` refuses it on
    ``walk_away``, where taking ``ask`` as a lower bound would have returned 0 and made "too
    expensive" indistinguishable from "never considered".

    Measured on the shipped golden pool (2026-08-28, ``lam=0.3``, empty state, plan objective
    2251.8): a solve here costs a handful of milliseconds, and one lot a cycle — the room
    prices only the lot on the block, once per state — is nowhere near the cost of pricing the
    whole plan every poll.
    """
    lo, hi = int(MIN_BID), int(hard_cap)
    if hi < lo:
        return 0

    # One index for every probe below: same pool, same prices, same value model, same rules.
    # This is `reservations`' own reason for `build_index`, and the scan makes up to
    # `hard_cap` of these calls where it makes one.
    index = build_index(pool, prices, value, rules)
    margin = max(margin_abs, margin_rel * abs(value.value(player_id).mean))

    def better_at(price: int) -> bool:
        """Is the rosa we can still build, having bought him at ``price``, worth more?

        An ``InfeasibleRoster`` is a "no" and not an error: it is this function catching the
        case `max_bid` cannot see, where the credits left will not complete the band. That is
        also the only guard between this ceiling and a rosa that cannot be filled.
        """
        forced = replace(
            state,
            owned=(*state.owned, player_id),
            spent=state.spent + price,
            taken=state.taken | {player_id},
        )
        try:
            improved = optimize_roster(
                forced, pool, value=value, prices=prices, teams=teams, legality=legality,
                rules=rules, lam=lam, rho=rho, n_fallbacks=0, index=index,
            ).optimal.objective
        except InfeasibleRoster:
            return False
        return improved >= baseline + margin

    return safe_ceiling(better_at, lo=lo, hi=hi)


def reservations(
    state: AstaState,
    pool: Sequence[Candidate],
    *,
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: CompositionRules = RosterRules(),
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

    **This walk-away is advisory only, never the bid decision (Task 1.3).** It used to carry
    an injected walk-away floor, a fraction of book price, precisely because a bare
    ``base - alt`` collapses to 0 whenever a near-substitute exists — the unit-error `SPEC.md`
    §2.A measured. `RoomTracker._decide` no longer reads this dict for the lot it is actually
    deciding on; every lot, plan member or not, is priced by `lot_ceiling`/`lot_reference`
    instead, an honest re-solve rather than a floored approximation. What this function still
    prices, unfloored, is the LISTONE table and the copilot brief — a reading aid for up to
    forty targets a cycle, where a full re-solve per target is not the cost the design accepts.
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
            marginal = max(0.0, base - alt)
            walkaways[target] = min(state.remaining_budget, marginal)
        except InfeasibleRoster:
            walkaways[target] = state.remaining_budget
    return result, walkaways


def rolling_advisory(
    state: AstaState,
    pool: Sequence[Candidate],
    events: Iterable[AssignmentEvent],
    *,
    our_team_id: str,
    value_of: Callable[[], ValueModel],
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: CompositionRules = RosterRules(),
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

    ``asta live`` is purely advisory (a human bids by hand) and has no per-lot re-solve to
    keep in step with, so this shows the same unfloored ``reservations()`` marginal as any
    other reading of ``walkaways`` — see its docstring.
    """
    for event in events:
        state = apply_event(state, event, our_team_id=our_team_id)
        result, walkaways = reservations(
            state, pool, value=value_of(), prices=prices, teams=teams, legality=legality,
            rules=rules, lam=lam, rho=rho,
        )
        yield state, event, result, walkaways
