"""The opponent's effect on a player's fantavoto, and the adjustment it gives. Pure.

The projection's μ is a player's own average over many opponents, so it knows nothing about
who he faces this week. Adding `beta_role * (x_opponent - centre_role)` to it, with `x` the
opponent's expected points a game (`strength.py`), lowered the out-of-sample squared error of
the next appearance's lega fantavoto by about 1% in both 2024/25 and 2025/26 (cluster-bootstrap
CIs by fixture exclude 0 in each; a club-label permutation test p ≤ 0.0002, the resolution
floor of 5,000 one-sided permutations, which was run for the experiment's rank and
expected-points variants, V1 and V2 in SPEC A26, and not for the estimator shipped here). The
gain sits in goalkeepers and attackers, with the same sign in every role and every season. Home
or away adds nothing measurable and is left out. Two independent audits found the experiment
leak-free.

How much a player moves, weakest to strongest opponent met in the test season, with this
estimator's own fits (2024/25, then 2025/26): GK 1.32 and 1.27 points, ATT 0.81 and 0.76,
MID_ATT 0.82 and 0.64, MID 0.44 and 0.39, DEF 0.36 and 0.31. The experiment's rank variant
gave GK 1.36 and 1.35 and its expected-points variant 1.44 and 1.36; across the three no role
differs by more than 0.12 point.

**The estimator is the within-(player, season) slope** — OLS of the score on `x` after
demeaning both inside each player-season, per macro role. It needs no μ, so it is fitted from
the history the projection already reads in a few milliseconds, where the residual
regression the experiment used needed one `project()` refit per giornata (about 11 s). On the
same folds, test rows and bootstrap it was statistically indistinguishable: ΔMSE against the
unadjusted μ, pooled over the two test seasons, was -0.0259 [-0.0387, -0.0133] for this
estimator and -0.0252 for the residual one (their difference, -0.0007, has a CI whose upper end
is 0.0000). Demeaning within the player-season removes the player level, season effects
included. That matters when the level is correlated with the opponents a player meets: on data
with that planted (slope -0.7), the estimator's self-test finds pooled OLS 0.875 off while the
within estimator recovers it. No pooled fit on the real scores was run, so how large the bias
is on them is not measured.

**Leak-free for live use, not a point-in-time replay.** Every historical row's `x` is computed
as of the first match of its own giornata, from results dated strictly before it, and the live
opponent's strength uses only results dated before `as_of`. Each completed season's value map is
fitted on the *other* completed seasons, so no season prices itself, but those include the later
ones, and every completed season trains the slope. For this week's plan that costs nothing,
because every completed season precedes `as_of`. For a past matchday it is lookahead, so a
backtest that reused `adjust` would inherit it: SPEC A26's FE-current, which recomputes every
historical feature with only the earlier seasons' map, is the strict replay, and this is not it.
Only completed seasons train the slope; the current one supplies the live opponent's strength.

**It fails closed per role** (`fit_roles`): too few rows, no variation in `x`, a slope that is
not negative (a stronger opponent should lower the score) or implausibly steep leaves that
role unadjusted, with the reason, and never touches the others.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from fantabot.domain.lineup.history import (
    Fixture,
    HistoryAppearance,
    Valuation,
    first_match_date,
    side,
)
from fantabot.domain.lineup.pairing import Pairing
from fantabot.domain.lineup.strength import (
    SeasonTable,
    ValueMap,
    expected_ppg,
    fit_value_map,
    season_table,
)
from fantabot.domain.shared.club_names import code_for

#: A role is not fitted on fewer rows than this. The thinnest role in the measurement (goal-
#: keepers on the 2022/23-2023/24 fold) had 846, with a fixture-clustered SE of 0.14 on a
#: slope of -1.02; extrapolating as 1/sqrt(n), 300 rows puts that SE near 0.24. Declared,
#: not fitted: it is a floor on how little the fit is trusted, not a tuned value.
MIN_ROLE_ROWS = 300

#: A fitted |slope| above this is refused. It is a coarse backstop and not a calibrated bound:
#: the steepest role is goalkeepers, at -0.88 on the live database and about -1.25 on the
#: steepest fold of any variant the experiment tried, so a slope of 3 points per point of
#: opponent strength is not a stronger effect, it is a join gone wrong, and at 3 a one-point
#: swing in `x` would move a player by more than a clean sheet. A join that is wrong by less
#: than that would pass it.
MAX_ABS_BETA = 3.0

#: Below this the within-player-season variation in `x` is rounding, not information.
_MIN_VARIATION = 1e-9

_ROLE_ORDER = ("GK", "DEF", "MID", "MID_ATT", "ATT")


@dataclass(frozen=True, slots=True)
class TrainingRow:
    """One historical appearance: the score, and how strong the opponent looked beforehand."""

    player_id: int
    season: str
    #: Macro role of the player, the unit of the fit.
    macro: str
    #: The lega fantavoto of the appearance.
    score: float
    #: The opponent's expected points a game as of the first match of the giornata.
    opponent_ppg: float


@dataclass(frozen=True, slots=True)
class RoleEffect:
    """One role's fitted effect of the opponent."""

    #: Points of fantavoto per point of the opponent's expected points a game. Negative.
    beta: float
    #: The role's mean `x` over its training rows: the opponent that moves nobody.
    centre: float
    #: Rows the role had, the centre's denominator.
    rows: int


@dataclass(frozen=True, slots=True)
class Fit:
    """What `fit_roles` could and could not fit."""

    effects: Mapping[str, RoleEffect]
    #: Per role that was **not** fitted, why.
    refused: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Adjustment:
    """What one roster player's μ moves by this week, and why."""

    player_id: int
    own: str
    opponent: str
    #: The opponent's expected points a game at `as_of`.
    opponent_ppg: float
    #: Added to μ before the sub-aware term, the XI and the evaluator see it.
    delta: float


@dataclass(frozen=True, slots=True)
class Matchups:
    """The adjustments for the roster, and one sentence saying how they came about."""

    adjustments: Mapping[int, Adjustment]
    #: The slopes and head count when anyone was adjusted, the reason when no one was.
    note: str


def delta(effect: RoleEffect, opponent_ppg: float) -> float:
    """The change to a player's μ against an opponent of the given strength."""
    return effect.beta * (opponent_ppg - effect.centre)


def fit_roles(rows: Iterable[TrainingRow], *, min_rows: int | None = None) -> Fit:
    """The within-(player, season) slope of the score on the opponent's strength, per role.

    Args:
        rows: Training appearances. A player-season with a single row has no within variation
            and is left out of the slope, but still informs the role's centre.
        min_rows: The floor on a role's rows; `MIN_ROLE_ROWS` when not given.

    Returns:
        The roles that could be fitted, and for each that could not, the reason.
    """
    floor = MIN_ROLE_ROWS if min_rows is None else min_rows
    by_role: dict[str, list[TrainingRow]] = defaultdict(list)
    for row in rows:
        by_role[row.macro].append(row)
    effects: dict[str, RoleEffect] = {}
    refused: dict[str, str] = {}
    for role, members in by_role.items():
        outcome = _fit_role(members, floor)
        if isinstance(outcome, str):
            refused[role] = outcome
        else:
            effects[role] = outcome
    return Fit(effects=effects, refused=refused)


def _fit_role(members: Sequence[TrainingRow], floor: int) -> RoleEffect | str:
    """One role's effect, or the reason it was refused."""
    n = len(members)
    if n < floor:
        return f"{n} row(s), fewer than {floor}"
    groups: dict[tuple[str, int], list[TrainingRow]] = defaultdict(list)
    for row in members:
        groups[row.season, row.player_id].append(row)
    sxx = sxy = 0.0
    # A player-season with one row contributes nothing to either sum (it is its own mean), so
    # it needs no special case here; it still counts toward the centre below.
    for group in groups.values():
        mean_x = math.fsum(r.opponent_ppg for r in group) / len(group)
        mean_y = math.fsum(r.score for r in group) / len(group)
        for r in group:
            dx = r.opponent_ppg - mean_x
            sxx += dx * dx
            sxy += dx * (r.score - mean_y)
    if sxx <= _MIN_VARIATION:
        return "no variation in the opponent's strength within a player-season"
    beta = sxy / sxx
    if not math.isfinite(beta):
        return "the slope is not finite"
    if beta >= 0.0:
        return f"wrong sign: slope {beta:+.3f}, a stronger opponent should lower the score"
    if abs(beta) > MAX_ABS_BETA:
        return f"implausible slope {beta:+.2f}, beyond ±{MAX_ABS_BETA:g}"
    return RoleEffect(
        beta=beta, centre=math.fsum(r.opponent_ppg for r in members) / n, rows=n
    )


def adjust(
    *,
    scored: Sequence[tuple[HistoryAppearance, float]],
    valuations: Mapping[str, Mapping[int, Valuation]],
    fixtures: Mapping[str, Sequence[Fixture]],
    macro: Mapping[int, str],
    pairings: Mapping[int, Pairing],
    season: str,
    cmday: int,
    as_of: date,
    min_role_rows: int | None = None,
) -> Matchups:
    """How much each paired roster player's μ should move against this week's opponent.

    Args:
        scored: Every history appearance the projection read, each with its lega fantavoto.
            Dated before `as_of` by the read that produced it.
        valuations: Preseason `qi` and club per player, per season, for every season read.
        fixtures: Every match played per season, for the same seasons.
        macro: The macro role of every player the history was read for.
        pairings: Each roster player's upcoming match, from the lineup DTO.
        season: The season being played.
        cmday: The Serie A giornata the lineup is for; 0 when it has none yet.
        as_of: The day the plan is for. The opponent's strength uses results before it.
        min_role_rows: The floor on a role's training rows; `MIN_ROLE_ROWS` when not given.

    Returns:
        The adjustment of every player that could be priced, and a note saying what was done
        or why nothing was. A player left out was left unadjusted, never guessed at.
    """
    if cmday <= 0:
        return _none("the lineup has no matchday yet, so no opponent to price")
    if not pairings:
        return _none("no roster player has a readable pairing in the lineup")
    # Sorted: a set of season strings iterates in PYTHONHASHSEED order and `fit_value_map` sums
    # in the order it is handed, so unsorted the same inputs give deltas that differ in the
    # last bit from one process to the next.
    tables = {
        s: season_table(s, valuations.get(s, {}), fixtures.get(s, ()))
        for s in sorted({*valuations, *fixtures})
    }
    current = tables.get(season)
    if current is None:
        return _none(f"no squad values for {season}")
    completed = {s: t for s, t in tables.items() if t is not None and s < season and t.complete}
    if not completed:
        return _none("no completed season to fit on")
    live_map = fit_value_map(list(completed.values()))
    if live_map is None:
        return _none("the squad-value map could not be fitted on the completed seasons")

    rows = _training_rows(scored, valuations, fixtures, completed, macro)
    fit = fit_roles(rows, min_rows=min_role_rows)
    refusals = ", ".join(f"{role} {why}" for role, why in sorted(fit.refused.items()))
    if not fit.effects:
        return _none("no role has a usable slope" + (f" ({refusals})" if refusals else ""))

    # The lineup DTO keeps naming the round just played until the platform advances it
    # overnight. This drops such a pairing only when `fixtures` already holds that round's
    # results, and they arrive with the voti refresh (`match_grain`), so before it a played
    # round is priced as if upcoming. For a scheduled run the guard is `lineup_submit`'s
    # `scheduled_cutoff` (matchday-mismatch, matchday-started); a manual run prices the DTO's
    # own round, played or not.
    finished = {
        frozenset((code_for(f.home), code_for(f.away)))
        for f in fixtures.get(season, ())
        if f.giornata == cmday and f.played_on < as_of
    }
    adjustments: dict[int, Adjustment] = {}
    left: dict[str, int] = defaultdict(int)
    unknown: set[str] = set()
    for pid, pairing in pairings.items():
        role = macro.get(pid)
        if role is None:
            left["with no macro role"] += 1
        elif (effect := fit.effects.get(role)) is None:
            left["in a role with no usable slope"] += 1
        elif frozenset((pairing.own, pairing.opponent)) in finished:
            left["in a match already played"] += 1
        elif (x := expected_ppg(current, live_map, pairing.opponent, before=as_of)) is None:
            left["against an unknown club"] += 1
            unknown.add(pairing.opponent)
        else:
            adjustments[pid] = Adjustment(
                player_id=pid, own=pairing.own, opponent=pairing.opponent,
                opponent_ppg=x, delta=delta(effect, x),
            )

    slopes = ", ".join(
        f"{role} {fit.effects[role].beta:+.2f}" for role in _ordered(fit.effects)
    )
    parts = [
        f"slope per point of opponent ppg: {slopes} "
        f"({len(rows)} appearances over {len(completed)} completed seasons)",
        f"{len(adjustments)} of {len(pairings)} paired players adjusted",
    ]
    parts += [f"{count} {why}" for why, count in sorted(left.items())]
    if unknown:
        parts.append(f"unknown: {', '.join(sorted(unknown))}")
    if refusals:
        parts.append(f"refused: {refusals}")
    return Matchups(adjustments=adjustments, note="; ".join(parts))


def _none(reason: str) -> Matchups:
    return Matchups(adjustments={}, note=reason)


def _ordered(roles: Iterable[str]) -> list[str]:
    """Roles in the order the operator reads them, any unknown ones last."""
    present = set(roles)
    return [r for r in _ROLE_ORDER if r in present] + sorted(present - set(_ROLE_ORDER))


def _training_rows(
    scored: Sequence[tuple[HistoryAppearance, float]],
    valuations: Mapping[str, Mapping[int, Valuation]],
    fixtures: Mapping[str, Sequence[Fixture]],
    completed: Mapping[str, SeasonTable],
    macro: Mapping[int, str],
) -> list[TrainingRow]:
    """Each completed-season appearance with the opponent's strength the day its giornata opened.

    A season's value map is fitted on the *other* completed seasons. A row whose club does not
    resolve to an end of its match, whose player has no valuation that season or no macro
    role, or whose opponent has no strength is dropped rather than guessed.
    """
    maps: dict[str, ValueMap] = {}
    for s in completed:
        fitted = fit_value_map([t for other, t in completed.items() if other != s])
        if fitted is not None:
            maps[s] = fitted
    # Built once so the loop over tens of thousands of rows below is a lookup; the definition
    # of the day a giornata opens stays `history.first_match_date`'s, the one the backtest uses.
    opens: dict[tuple[str, int], date] = {
        (s, g): first_match_date(fixtures.get(s, ()), season=s, giornata=g)
        for s in completed
        for g in {f.giornata for f in fixtures.get(s, ())}
    }

    out: list[TrainingRow] = []
    for row, score in scored:
        f = row.fixture
        value_map = maps.get(f.season)
        role = macro.get(row.player_id)
        valuation = valuations.get(f.season, {}).get(row.player_id)
        cutoff = opens.get((f.season, f.giornata))
        if value_map is None or role is None or valuation is None or cutoff is None:
            continue
        end = side(f, valuation.squadra)
        if end is None:
            continue
        x = expected_ppg(
            completed[f.season], value_map,
            code_for(f.away if end == "home" else f.home), before=cutoff,
        )
        if x is not None:
            out.append(TrainingRow(row.player_id, f.season, role, score, x))
    return out
