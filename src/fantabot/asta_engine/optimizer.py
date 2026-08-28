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

from .legality import SchemaLegality, SlotRule, fieldable_schemi
from .roles import MantraPlayer
from .state import AstaState, OptimizationResult, Roster, RosterRules
from .value import ValueModel

#: The same-club correlation the naive variance uses. A placeholder until the covariance is
#: measured from the four seasons of voti (or supplied by the skfolio value layer).
DEFAULT_SAME_TEAM_RHO = 0.5

#: What an unpriced player is assumed to cost — the 1-credit riserva that always exists.
DEFAULT_PRICE = 1


class InfeasibleRoster(RuntimeError):
    """No roster satisfying budget, composition and a legal XI could be built."""


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
    picked: Sequence[str],
    value: ValueModel,
    teams: Mapping[str, str],
    lam: float,
    rho: float,
) -> float:
    v = value.value(player_id)
    penalty = v.variance
    team = teams.get(player_id)
    if team is not None:
        sigma = math.sqrt(v.variance)
        for other in picked:
            if teams.get(other) == team:
                penalty += 2 * rho * sigma * math.sqrt(value.value(other).variance)
    return v.mean - lam * penalty


def _submission_eligible(player: MantraPlayer, slot: SlotRule) -> bool:
    return any(role in slot.submission for role in player.roles)


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
    budget = budget_left
    ordered = sorted(
        schema.slots,
        key=lambda s: sum(1 for p in available if p.id not in used and _submission_eligible(p, s)),
    )
    for slot in ordered:
        owned_match = next((p for p in owned_left if _submission_eligible(p, slot)), None)
        if owned_match is not None:
            owned_left.remove(owned_match)
            continue
        reserve = rules.size - len(owned_ids) - len(seed) - 1
        candidates = [
            p
            for p in available
            if p.id not in used
            and _submission_eligible(p, slot)
            and _cost(p.id, prices) <= budget - reserve
        ]
        if not candidates:
            return None
        picked_now = [*owned_ids, *seed]
        best = max(
            candidates,
            key=lambda p: _marginal_gain(p.id, picked_now, value, teams, lam, rho),
        )
        seed.append(best.id)
        used.add(best.id)
        budget -= _cost(best.id, prices)
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
) -> tuple[list[str], float] | None:
    for schema in legality.values():
        seeded = _seed_schema(
            schema, owned_players, budget_left, available, value, prices, teams, rules, lam, rho
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
) -> Roster:
    picked: list[str] = list(state.owned)
    picked_set = set(picked)
    budget_left = state.remaining_budget

    # Guarantee a legal XI by construction: seed one schema's slots unless what we already
    # own can field a schema on its own. Value-first greedy alone neglects role coverage and
    # produces rose that field nothing.
    owned_players = [by_id[pid] for pid in picked]
    if not fieldable_schemi(owned_players, legality):
        seeded = _seed_legal_xi(
            owned_players, budget_left, available, value, prices, teams, legality, rules, lam, rho
        )
        if seeded is None:
            raise InfeasibleRoster("no schema can be seeded within budget")
        seed_ids, budget_left = seeded
        picked.extend(seed_ids)
        picked_set.update(seed_ids)

    goalkeepers = sum(1 for pid in picked if _is_goalkeeper(by_id[pid], rules))

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
            if _cost(player.id, prices) > budget_left - reserve:
                continue
            is_gk = _is_goalkeeper(player, rules)
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
            key=lambda p: _marginal_gain(p.id, picked, value, teams, lam, rho),
        )
        picked.append(best.id)
        picked_set.add(best.id)
        budget_left -= _cost(best.id, prices)
        if _is_goalkeeper(best, rules):
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
) -> OptimizationResult:
    """Build the current optimal roster, plus a next-best plan for each top target lost.

    ``pool`` must carry every referenced player (owned and available). ``lam`` is the risk
    knob: 0 maximizes raw points, higher diversifies across clubs.
    """
    by_id = {p.id: p for p in pool}
    owned = set(state.owned)
    available = [p for p in pool if p.id not in state.taken and p.id not in owned]

    optimal = _build(state, by_id, available, value, prices, teams, legality, rules, lam, rho)

    fallbacks: list[Roster] = []
    for target in _fallback_targets(optimal, owned, value, n_fallbacks):
        without = [p for p in available if p.id != target]
        try:
            fallbacks.append(
                _build(state, by_id, without, value, prices, teams, legality, rules, lam, rho)
            )
        except InfeasibleRoster:
            continue

    return OptimizationResult(optimal, tuple(fallbacks))
