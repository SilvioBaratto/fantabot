"""The projection plan: what the roster is worth by the model, and the XI that follows.

The default path values a player at the platform's own `indexCompare`. This one reads the
history and builds, per player, μ (what he scores when he plays, T15), p (whether he plays at
all, T16) and `sigma_tilde` (the spread, which the Monte Carlo of phase 4 will use). The
matcher then ranks the modules on the **sub-aware** term `p·μ + (1-p)·v` — the auto-sub is
what a non-voting starter is replaced by, so `v` is the man who would come on
(`domain/lineup/value.sub_aware`).

Three things here are decisions, not plumbing.

**The population is the listone, not the roster.** The prior is a fit across players on role,
`qi` and club, and 30 of them are too few to fit it on; the platform's own listone is ~595.
The roster is only the subset the lines and the XI are read back for.

**It reads only before `as_of`, and `as_of` is given.** The read itself is bounded
(`appearances(..., before=as_of)`), not just the fold: a leak here would be invisible in the
backtest, where every giornata is replayed from a database that already holds the answer.
`interface/lineup.py::_now` is the one clock.

⚠ **One `v` for every slot over-credits a player who will not play.** At `p = 0` the term is
exactly `v`: his slot is worth whatever the man who comes on is worth, so an injured starter
is benched only when his μ is below the replacement's. That is true only if the sub actually
fires — one reserve cannot cover two vacancies, and the lega's substitution budget is finite.
A17(3)'s per-slot `v_s` (T22) and the real engine under Monte Carlo (T28) are what settle it;
until then `p` is in the printed table, where the operator can see it.

**Staleness is a verdict, not a gate.** It says whether the voti behind μ are final (T25); the
fallback that acts on it is `--shadow`'s, in phase 6. A lineup with no matchday coordinates
yet has nothing to be fresh *for*, and says that rather than reading as fresh.

numpy and scipy live behind this module (`domain/lineup/projection`), so the default path must
never import it. `tests/domain/lineup/test_lineup_imports.py` declares the one edge that may
reach it, from `interface/lineup.py`'s command body.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from fantabot.application.lineup_planner import plan_lineups
from fantabot.application.scrape import current_season
from fantabot.domain.asta.roles import macro_role
from fantabot.domain.lineup.freshness import Freshness, staleness
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, LineupHistory, Valuation
from fantabot.domain.lineup.presence import PresenceWeights, presence, windows
from fantabot.domain.lineup.projection import (
    Projection,
    ProjectionConfig,
    Target,
    observations,
    project,
)
from fantabot.domain.lineup.value import replacement_level, sub_aware

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_planner import LineupInputs
    from fantabot.domain.lineup.models import PlannedLineup
    from fantabot.domain.lineup.scoring import ScoringRules

#: Seasons of history the projection reads, the current one included.
SEASONS_READ = 5
#: H, in days. A declared default until T33's sweep picks one on 2023/24 and records it;
#: 180 is the value T15 measured the model at (τ² = 0.041).
DEFAULT_HALF_LIFE_DAYS = 180.0
#: A macro role's Classic letter, for a player with no appearance of his own to read one
#: from. Measured on the 2026/27 listone: every DEF code is `D` and every ATT code `A`;
#: `W`/`T` are `C` for 62 of 74, and `E` for 11 of 18.
MACRO_TO_CLASSIC = {"GK": "P", "DEF": "D", "MID": "C", "MID_ATT": "C", "ATT": "A"}
NO_MATCHDAY = (
    "the lineup has no matchday coordinates yet, so there is no giornata to be fresh for"
)


@dataclass(frozen=True, slots=True)
class PlayerLine:
    """One roster player as the model sees him — the row the operator reads at CP2."""

    player_id: int
    macro: str
    #: His Classic role, which is the scale presence's own priors are on.
    role: str
    mu: float
    p: float
    sigma_tilde: float
    #: `p·μ + (1-p)·v`: what the matcher ranks on.
    value: float


@dataclass(frozen=True, slots=True)
class ProjectionOutcome:
    plans: tuple[PlannedLineup, ...]
    #: The roster, best value first.
    lines: tuple[PlayerLine, ...]
    freshness: Freshness
    #: `v`, the replacement level the sub-aware term priced against.
    replacement: float
    as_of: date
    seasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    """One lega, both models: what the default path would field, and what the model would."""

    #: The `indexCompare` plans, best first — the default path's own, unchanged.
    baseline: tuple[PlannedLineup, ...]
    projection: ProjectionOutcome
    names: Mapping[int, str]
    competition: int


def projection_for_league(
    store: TokenStore,
    *,
    league_id: int,
    competition: int,
    as_of: date,
    voti_refreshed: bool = False,
    config: ProjectionConfig | None = None,
    weights: PresenceWeights = PresenceWeights(),
) -> ProjectionReport:
    """Both plans for one lega, from one set of reads.

    The history is read in **its own session**: it is the large read (tens of thousands of
    rows) and it has nothing to do with the token session the HTTP reads run under, which
    stays short. Both are read-only.
    """
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.lineup_history import (
        LineupHistoryRepository,
    )
    from fantabot.application.lineup_submit import build_inputs
    from fantabot.domain.lineup.scoring import ScoringRules as Rules

    inputs, names, comp = build_inputs(store, league_id, competition)
    calculate = apileague.calculate_settings(league_id, store=store)
    rules = Rules.from_settings(calculate["bnMls"], calculate["step"])
    with database_manager.get_session() as session:
        outcome = plan_projection(
            inputs,
            LineupHistoryRepository(session),
            as_of=as_of,
            rules=rules,
            voti_refreshed=voti_refreshed,
            config=config,
            weights=weights,
        )
    return ProjectionReport(
        baseline=tuple(plan_lineups(inputs)), projection=outcome, names=names, competition=comp
    )


def plan_projection(
    inputs: LineupInputs,
    history: LineupHistory,
    *,
    as_of: date,
    rules: ScoringRules,
    voti_refreshed: bool = False,
    config: ProjectionConfig | None = None,
    weights: PresenceWeights = PresenceWeights(),
) -> ProjectionOutcome:
    """Project the roster and rank the modules on it. Reads history, opens nothing."""
    season = current_season(as_of)
    seasons = _seasons(season)
    valuations = {s: history.valuations(s) for s in seasons}
    targets = _targets(inputs, history.roles(season), valuations.get(season, {}))
    rows = history.appearances(sorted(targets), seasons=seasons, before=as_of)
    fixtures = {s: history.fixtures(s) for s in seasons}

    projections = project(
        observations(rows, rules=rules, valuations=valuations),
        targets,
        as_of=as_of,
        config=config or ProjectionConfig(DEFAULT_HALF_LIFE_DAYS),
    )
    classic = _classic_roles(rows, targets)
    presences = presence(
        windows(
            rows,
            [f for season_fixtures in fixtures.values() for f in season_fixtures],
            valuations,
            targets,
            cutoff=as_of,
            k=weights.k,
        ),
        classic,
        history.latest_sentiment(sorted(targets)),
        as_of=as_of,
        weights=weights,
    )

    roster = [pid for pid in inputs.roster_ids if pid in projections]
    mu = {pid: projections[pid].mu for pid in roster}
    p = {pid: presences[pid].p for pid in roster}
    values = sub_aware(mu, p)
    return ProjectionOutcome(
        plans=tuple(plan_lineups(replace(inputs, fvmma_by_id=values))),
        lines=_lines(roster, targets, classic, projections, p, values),
        freshness=_freshness(fixtures[season], cmday=inputs.cmday, refreshed=voti_refreshed),
        replacement=replacement_level({pid: p[pid] * mu[pid] for pid in roster}),
        as_of=as_of,
        seasons=seasons,
    )


def _seasons(current: str, count: int = SEASONS_READ) -> tuple[str, ...]:
    """`count` seasons ending at `current`, oldest first."""
    start = int(current.split("/")[0])
    return tuple(f"{y}/{(y + 1) % 100:02d}" for y in range(start - count + 1, start + 1))


def _targets(
    inputs: LineupInputs, listone: Mapping[int, Sequence[str]], current: Mapping[int, Valuation]
) -> dict[int, Target]:
    """The listone, plus any roster player it does not hold, read through his own roles.

    A player with no role codes at all is left out: he has no macro role to fit a prior on,
    and `assemble_roster` refuses him a few lines later anyway.
    """
    system = "classic" if inputs.fmt == "classic" else "mantra"
    codes_by_id = {pid: tuple(codes) for pid, codes in listone.items()}
    for pid in inputs.roster_ids:
        if pid not in codes_by_id:
            codes_by_id[pid] = tuple(inputs.roles_by_id.get(pid, ()))
    targets: dict[int, Target] = {}
    for pid, codes in codes_by_id.items():
        if not codes:
            continue
        valuation = current.get(pid)
        targets[pid] = Target(
            player_id=pid,
            macro=macro_role(";".join(codes), system),
            qi=valuation.qi if valuation else None,
            club=valuation.squadra if valuation else None,
        )
    return targets


def _classic_roles(
    rows: Sequence[HistoryAppearance], targets: Mapping[int, Target]
) -> dict[int, str]:
    """Each player's Classic letter: his latest appearance's, else his macro role's.

    `match_grain` records what he was fielded as, which is the scale `presence`'s declared
    priors are on; a player with no Serie A appearance takes the map.
    """
    latest: dict[int, tuple[object, str]] = {}
    for row in rows:
        seen = latest.get(row.player_id)
        if seen is None or row.fixture.played_on >= seen[0]:  # type: ignore[operator]
            latest[row.player_id] = (row.fixture.played_on, row.role)
    return {
        pid: latest[pid][1] if pid in latest else MACRO_TO_CLASSIC[target.macro]
        for pid, target in targets.items()
    }


def _lines(
    roster: Sequence[int],
    targets: Mapping[int, Target],
    classic: Mapping[int, str],
    projections: Mapping[int, Projection],
    p: Mapping[int, float],
    values: Mapping[int, float],
) -> tuple[PlayerLine, ...]:
    lines = [
        PlayerLine(
            player_id=pid,
            macro=targets[pid].macro,
            role=classic[pid],
            mu=projections[pid].mu,
            p=p[pid],
            sigma_tilde=projections[pid].sigma_tilde2 ** 0.5,
            value=values[pid],
        )
        for pid in roster
    ]
    return tuple(sorted(lines, key=lambda line: line.value, reverse=True))


def _freshness(fixtures: Sequence[Fixture], *, cmday: int, refreshed: bool) -> Freshness:
    if cmday <= 0:
        return Freshness(fresh=False, reasons=(NO_MATCHDAY,), warnings=())
    per_giornata = Counter(f.giornata for f in fixtures)
    return staleness(
        max_giornata=max(per_giornata, default=0),
        cmday=cmday,
        fixtures_per_giornata=per_giornata,
        voti_refreshed=refreshed,
    )
