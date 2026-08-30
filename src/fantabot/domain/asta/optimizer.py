"""The roster optimizer: greedy mean-variance selection under budget, composition and L1.

Objective for a roster R (the utility of the team's total season points):

    obj(R) = sum_i mu_i  -  lam * Var(R)
    Var(R) = sum_i var_i  +  sum_{i<j, same club} 2 * rho * sigma_i * sigma_j

The same-club covariance term is the diversification lever: a higher lam penalizes holding
several players from one club, as portfolio theory says correlated holdings raise a team's
variance. rho is a fixed same-club correlation for the naive v1.

The builder is greedy — it adds the player with the best marginal gain, honours
the goalkeeper/movement composition, reserves one credit per remaining slot so the rosa can
always be completed, and refuses to return a roster that cannot field a legal XI (L1). It is
not the exact optimum; a MIQP with the legal-XI constraint is a later refinement. Everything
here is pure — it takes the value model, prices, teams and legality as arguments.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from fantabot.domain.asta.legality import SchemaLegality, SlotRule, fieldable_schemi
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, OptimizationResult, Roster, RosterRules
from fantabot.domain.asta.value import ValueModel

#: The same-club correlation the naive variance uses. A placeholder until the covariance is
#: measured from the four seasons of voti (or supplied by the skfolio value layer).
DEFAULT_SAME_TEAM_RHO = 0.5

#: What an unpriced player is assumed to cost — the 1-credit riserva that always exists.
DEFAULT_PRICE = 1


class InfeasibleRoster(RuntimeError):
    """No roster satisfying budget, composition and a legal XI could be built."""


def build_index(
    pool: Sequence[MantraPlayer],
    prices: Mapping[str, float],
    value: ValueModel,
    rules: RosterRules | None = None,
) -> _Index:
    """A reusable index for callers that solve the same pool more than once.

    It depends only on the pool, the prices, the value model and the roster rules —
    none of which changes between the solves inside one cycle.
    """
    return _Index(pool, prices, rules or RosterRules(), value)


def _cost(player_id: str, prices: Mapping[str, float]) -> int:
    """Planning cost in credits: the auction never sells below 1, so neither do we."""
    return max(DEFAULT_PRICE, round(prices.get(player_id, float(DEFAULT_PRICE))))


def _is_goalkeeper(player: MantraPlayer, rules: RosterRules) -> bool:
    return bool(player.roles & rules.goalkeeper_roles)


def objective(
    player_ids: Sequence[str],
    value: ValueModel,
    teams: Mapping[str, str],
    lam: float,
    rho: float = DEFAULT_SAME_TEAM_RHO,
) -> float:
    """The mean-variance utility of a roster. Pure."""
    mean = sum(value.value(pid).mean for pid in player_ids)
    variance = sum(value.value(pid).variance for pid in player_ids)
    for a in range(len(player_ids)):
        for b in range(a + 1, len(player_ids)):
            i, j = player_ids[a], player_ids[b]
            team = teams.get(i)
            if team is not None and team == teams.get(j):
                variance += 2 * rho * math.sqrt(value.value(i).variance * value.value(j).variance)
    return mean - lam * variance


def _marginal_gain(
    player_id: str,
    by_team: Mapping[str, list[float]],
    index: _Index,
    teams: Mapping[str, str],
    lam: float,
    rho: float,
) -> float:
    """What adding this player is worth, given who is already picked.

    ``by_team`` maps a club to the ``sqrt(variance)`` of every already-picked player
    from it, **in pick order**. It replaces a scan over the whole picked list, which
    made this O(|picked|) and called ``value.value`` once per pair — 86,831 lookups a
    cycle.

    **A bucket, not a running sum, and the difference is not stylistic.** SPEC
    prescribed keeping a running per-club total of ``sqrt(variance)`` and multiplying
    once. That is a different computation in IEEE-754: the loop below accumulates
    ``((2*rho)*sigma) * sqrt(var_i)`` term by term, and ``a*x + a*y`` is not
    ``a*(x+y)``. Measured over the real pool, the running sum moved 6,640 floats at
    ``lam 0.3`` and 9,018 at ``lam 1.0``. Same terms in the same order is what makes
    this bit-identical, and the golden harness is what proves it.

    The expression is left inline rather than hoisted to a constant for the same
    reason: the loop is now short enough that it costs nothing, and hoisting is one
    more thing a reader would have to check for float-equivalence.
    """
    penalty = index.variance[player_id]
    team = teams.get(player_id)
    if team is not None:
        sigma = index.sigma[player_id]
        for other_sigma in by_team.get(team, ()):
            penalty += 2 * rho * sigma * other_sigma
    return index.mean[player_id] - lam * penalty


def _sigma_by_team(
    player_ids: Sequence[str], index: _Index, teams: Mapping[str, str]
) -> dict[str, list[float]]:
    """`club -> sqrt(variance)` per picked player, in pick order.

    Order is load-bearing: it is the summation order `_marginal_gain` depends on.
    """
    buckets: dict[str, list[float]] = {}
    for player_id in player_ids:
        team = teams.get(player_id)
        if team is not None:
            buckets.setdefault(team, []).append(index.sigma[player_id])
    return buckets


def _remember(
    buckets: dict[str, list[float]], player_id: str, index: _Index,
    teams: Mapping[str, str],
) -> None:
    """Append one pick to its club's bucket, preserving order."""
    team = teams.get(player_id)
    if team is not None:
        buckets.setdefault(team, []).append(index.sigma[player_id])


def _submission_eligible(player: MantraPlayer, slot: SlotRule) -> bool:
    return any(role in slot.submission for role in player.roles)


class _Index:
    """Per-player facts the inner loops recompute, derived once and shared.

    None depends on what has been picked, yet all three were evaluated inside the
    candidate scan: `_cost` and `_is_goalkeeper` for every candidate on every pick,
    and `_submission_eligible` for every (player, slot) pair on every schema of every
    build.

    **Slot eligibility is computed on demand, not up front, and that is the whole
    design.** A first version precomputed all eleven schemas: 11 x 11 slots x 548
    players. It made the cycle *slower* — 45.5 ms to 90.0 ms — because
    `_seed_legal_xi` returns on the first schema that can be seeded, so most schemas
    are never consulted at all. Precomputing them bought work nobody asked for. The
    cache below pays for a slot the first time it is used and shares it across the six
    builds of one cycle, which is where the saving actually is.

    Purely a lookup table: `_cost` rounds, `_is_goalkeeper` is a set test, and
    `eligible` answers the same membership question as `any(role in slot.submission)`.
    Nothing here can move a number, and the golden harness is what says so.
    """

    __slots__ = ("_cache", "_pool", "cost", "is_goalkeeper", "mean", "sigma", "variance")

    def __init__(
        self,
        pool: Sequence[MantraPlayer],
        prices: Mapping[str, float],
        rules: RosterRules,
        value: ValueModel,
    ) -> None:
        self.cost: dict[str, int] = {p.id: _cost(p.id, prices) for p in pool}
        self.is_goalkeeper: dict[str, bool] = {p.id: _is_goalkeeper(p, rules) for p in pool}
        # `value.value` was called 52,597 times a cycle for two floats that never
        # change within one. The model is a pure function of the player id, so
        # reading it once per player is the same answer — and `sigma` is stored
        # rather than re-derived because `math.sqrt` of the same double is the same
        # double, and the bucket needs it on every append.
        values = {p.id: value.value(p.id) for p in pool}
        self.mean: dict[str, float] = {i: v.mean for i, v in values.items()}
        self.variance: dict[str, float] = {i: v.variance for i, v in values.items()}
        self.sigma: dict[str, float] = {i: math.sqrt(v.variance) for i, v in values.items()}
        self._pool = pool
        self._cache: dict[int, frozenset[str]] = {}

    def eligible(self, slot: SlotRule) -> frozenset[str]:
        """The ids `slot` admits at submission, computed once per slot."""
        key = id(slot)
        hit = self._cache.get(key)
        if hit is None:
            hit = frozenset(p.id for p in self._pool if _submission_eligible(p, slot))
            self._cache[key] = hit
        return hit


def _seed_schema(
    schema: SchemaLegality,
    owned_players: Sequence[MantraPlayer],
    budget_left: float,
    available: Sequence[MantraPlayer],
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    rules: RosterRules,
    lam: float,
    rho: float,
    index: _Index,
) -> tuple[list[str], float] | None:
    """Reserve a legal XI for one schema: one distinct player per slot, hardest slots first.

    A slot is covered by an already-owned player when one fits (free — no reservation);
    otherwise the best available player by marginal gain is reserved (so the seed
    respects the diversification penalty). A credit is kept for every roster slot still to
    fill. Returns the reserved available ids and the remaining budget, or ``None`` if any
    slot cannot be covered affordably — the caller then tries the next schema.
    """
    owned_left = list(owned_players)
    owned_ids = [p.id for p in owned_players]
    used: set[str] = set()
    seed: list[str] = []
    buckets = _sigma_by_team(owned_ids, index, teams)
    available_ids = {p.id for p in available}
    budget = budget_left
    # `used` is empty here — it is assigned one statement above and nothing has been
    # added yet — so the `p.id not in used` term this key used to carry was dead.
    # Removed rather than kept as a no-op, because a reader has to prove it is one.
    ordered = sorted(schema.slots, key=lambda s: len(index.eligible(s) & available_ids))
    for slot in ordered:
        admits = index.eligible(slot)
        owned_match = next((p for p in owned_left if p.id in admits), None)
        if owned_match is not None:
            owned_left.remove(owned_match)
            continue
        reserve = rules.size - len(owned_ids) - len(seed) - 1
        candidates = [
            p
            for p in available
            if p.id not in used and p.id in admits and index.cost[p.id] <= budget - reserve
        ]
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda p: _marginal_gain(p.id, buckets, index, teams, lam, rho),
        )
        seed.append(best.id)
        _remember(buckets, best.id, index, teams)
        used.add(best.id)
        budget -= index.cost[best.id]
    return seed, budget


def _seed_legal_xi(
    owned_players: Sequence[MantraPlayer],
    budget_left: float,
    available: Sequence[MantraPlayer],
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: RosterRules,
    lam: float,
    rho: float,
    index: _Index,
) -> tuple[list[str], float] | None:
    for schema in legality.values():
        seeded = _seed_schema(
            schema, owned_players, budget_left, available, value, prices, teams, rules,
            lam, rho, index,
        )
        if seeded is not None:
            return seeded
    return None


def _build(
    state: AstaState,
    by_id: Mapping[str, MantraPlayer],
    available: Sequence[MantraPlayer],
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    rules: RosterRules,
    lam: float,
    rho: float,
    index: _Index,
) -> Roster:
    picked: list[str] = list(state.owned)
    picked_set = set(picked)
    budget_left = state.remaining_budget
    buckets = _sigma_by_team(picked, index, teams)

    # Guarantee a legal XI by construction: seed one schema's slots unless what we already
    # own can field a schema on its own. Value-first greedy alone neglects role coverage and
    # produces rose that field nothing.
    owned_players = [by_id[pid] for pid in picked]
    if not fieldable_schemi(owned_players, legality):
        seeded = _seed_legal_xi(
            owned_players, budget_left, available, value, prices, teams, legality, rules,
            lam, rho, index,
        )
        if seeded is None:
            raise InfeasibleRoster("no schema can be seeded within budget")
        seed_ids, budget_left = seeded
        picked.extend(seed_ids)
        picked_set.update(seed_ids)
        for seeded_id in seed_ids:
            _remember(buckets, seeded_id, index, teams)

    goalkeepers = sum(1 for pid in picked if index.is_goalkeeper[pid])

    while len(picked) < rules.size:
        slots_left = rules.size - len(picked)
        movement = len(picked) - goalkeepers
        need_gk = max(0, rules.min_goalkeepers - goalkeepers)
        need_mov = max(0, rules.min_movement - movement)
        reserve = slots_left - 1  # keep 1 credit per other remaining slot

        candidates: list[MantraPlayer] = []
        for player in available:
            if player.id in picked_set:
                continue
            if index.cost[player.id] > budget_left - reserve:
                continue
            is_gk = index.is_goalkeeper[player.id]
            if is_gk and (goalkeepers >= rules.max_goalkeepers() or need_mov >= slots_left):
                continue
            if not is_gk and (movement >= rules.max_movement() or need_gk >= slots_left):
                continue
            candidates.append(player)

        if not candidates:
            raise InfeasibleRoster(
                f"cannot complete the roster: {len(picked)}/{rules.size} filled, "
                f"{budget_left:.0f} credits left"
            )

        best = max(
            candidates,
            key=lambda p: _marginal_gain(p.id, buckets, index, teams, lam, rho),
        )
        picked.append(best.id)
        picked_set.add(best.id)
        _remember(buckets, best.id, index, teams)
        budget_left -= index.cost[best.id]
        if index.is_goalkeeper[best.id]:
            goalkeepers += 1

    if not fieldable_schemi([by_id[pid] for pid in picked], legality):
        raise InfeasibleRoster("the completed roster cannot field a legal XI")

    total_cost = state.spent + (state.remaining_budget - budget_left)
    return Roster(tuple(picked), total_cost, objective(picked, value, teams, lam, rho))


def _fallback_targets(
    roster: Roster, owned: set[str], value: ValueModel, n: int
) -> list[str]:
    fresh = [pid for pid in roster.player_ids if pid not in owned]
    return sorted(fresh, key=lambda pid: value.value(pid).mean, reverse=True)[:n]


def optimize_roster(
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
    n_fallbacks: int = 5,
    index: _Index | None = None,
) -> OptimizationResult:
    """Build the current optimal roster, plus a next-best plan for each top target lost.

    ``pool`` must carry every referenced player (owned and available). ``lam`` is the risk
    knob: 0 maximizes raw points, higher diversifies across clubs.
    """
    by_id = {p.id: p for p in pool}
    missing = [pid for pid in state.owned if pid not in by_id]
    if missing:
        raise InfeasibleRoster(f"owned players are not in the pool: {missing}")
    owned = set(state.owned)
    available = [p for p in pool if p.id not in state.taken and p.id not in owned]

    # One index per call by default, shared by the optimal build and every fallback.
    # A caller that solves the same pool repeatedly — `reservations` does, once per
    # walk-away target — passes its own, so the per-player facts and the slot cache
    # are paid once per cycle instead of once per solve. `build_index` is the seam.
    if index is None:
        index = _Index(pool, prices, rules, value)

    optimal = _build(
        state, by_id, available, value, prices, teams, legality, rules, lam, rho, index
    )

    fallbacks: list[Roster] = []
    for target in _fallback_targets(optimal, owned, value, n_fallbacks):
        without = [p for p in available if p.id != target]
        try:
            fallbacks.append(
                _build(
                    state, by_id, without, value, prices, teams, legality, rules, lam, rho,
                    index,
                )
            )
        except InfeasibleRoster:
            continue

    return OptimizationResult(optimal, tuple(fallbacks))
