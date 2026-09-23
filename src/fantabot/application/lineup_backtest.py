"""Gate 1: replay three recorded seasons, two arms, and say whether the model is worth it.

SPEC A16 and Decision 15. Everything the gate needs is here except the decision itself,
which is the operator's at CP5.

**The two arms share everything but the plan.** Same rosters, same giornate, same absences,
same votes, same sub mode. What differs is how the XI was chosen — the baseline's `p·mu`
matcher against `choose_plan` — because that is the only thing the gate is asking about.

**Everything is refit before g, once per giornata and not once per roster.** The projection,
the presence priors and the copula are functions of the season and the cutoff, not of whose
roster is being planned, so 148 rosters share one fit and the replay costs 33 fits a season
rather than 4,884. That is not only speed: a per-roster fit would be fitted on a population
that changed with the roster, and two rosters at the same giornata would be planned against
two different models.

**News is ablated.** `latest_sentiment` returns nothing in a replay: there are no recorded
readings for 2023/24, and a model graded with a signal it will not have is a model graded on
a game it will not play.

**The cutoff is a date, never a giornata.** A postponed match keeps its giornata and is
played weeks later, so `giornata < g` lets a result from the future into a fit that must not
see it. `history.first_match_date` is the one definition, and `scripts/leak_battery.py`
mutates it to prove the tests notice.

**H is picked on 2023/24 and never on gated data** (A16). The sweep season is replayed at
each candidate half-life and the argmax is carried into the graded seasons unchanged — a
half-life tuned on 2024/25 and then graded on 2024/25 would be a model choosing its own exam.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING

import numpy as np

from fantabot.application.lineup_planner import LineupInputs, plan_lineups
from fantabot.application.lineup_projection import (
    DEFAULT_HALF_LIFE_DAYS,
    _classic_roles,
)
from fantabot.domain.asta.roles import macro_role, normalize_roles
from fantabot.domain.lineup.backtest import (
    BURN_IN,
    Fielded,
    Paired,
    baseline_inputs,
    field,
    pair,
    table,
)
from fantabot.domain.lineup.choose import Budget, PlanInputs, choose_plan, seed_for
from fantabot.domain.lineup.dependence import fit as fit_dependence
from fantabot.domain.lineup.dependence import residuals
from fantabot.domain.lineup.errors import LineupError
from fantabot.domain.lineup.gate import Interval, two_way_bootstrap
from fantabot.domain.lineup.history import before, first_match_date
from fantabot.domain.lineup.models import assemble_roster
from fantabot.domain.lineup.opponent import Opponent
from fantabot.domain.lineup.opponent import fit as fit_opponent
from fantabot.domain.lineup.presence import PresenceWeights, presence, windows
from fantabot.domain.lineup.projection import (
    Projection,
    ProjectionConfig,
    Target,
    observations,
    project,
)
from fantabot.domain.lineup.value import sub_aware
from fantabot.domain.shared.club_names import code_for

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from fantabot.application.reporting import Reporter
    from fantabot.domain.lineup.backtest_corpus import Corpus
    from fantabot.domain.lineup.history import (
        Fixture,
        HistoryAppearance,
        LineupHistory,
        Valuation,
    )
    from fantabot.domain.lineup.scoring import ScoringRules
    from fantabot.domain.lineup.substitution import SubMode

#: The giornata the model arm starts on. Before it the baseline arm plays alone, so the
#: model's opponent has scores to be fitted on (A16(6)).
FIRST_MODEL_GIORNATA = BURN_IN + 1
#: The last giornata of a 20-team Serie A season.
LAST_GIORNATA = 38
#: The season whose roles every replay uses (A16(3)) — the platform froze them in July 2026
#: and a season's own tags are not recorded anywhere we can read.
ROLES_SEASON = "2026/27"
#: The three half-lives the sweep tries. Three, because the sweep season is one season and a
#: finer grid would be fitting the sweep's own noise.
SWEEP_HALF_LIVES: tuple[float, ...] = (90.0, DEFAULT_HALF_LIFE_DAYS, 365.0)
#: The fantapunti guard: the model may lose this much a giornata and still pass (A16).
FANTAPUNTI_GUARD = -1.0

#: The bench the gate replays on, and **not** the lega's own twelve. The operator's call,
#: 2026-09-23, on a measurement the phase could not have guessed:
#:
#: A16(2) makes a player eligible only with a `quotazioni` row for the *replayed* season,
#: and the corpus rosters were bought in a 2026/27 auction. Rosters that can actually field
#: an XI plus a bench, over all 148:
#:
#:     bench        1     3     5     8    12
#:     2023/24     39    36    24     6     0
#:     2024/25     86    85    76    47    12
#:     2025/26    109   109   109    99    56
#:
#: At twelve the **sweep season fields nothing at all**, so a run at the lega's own bench
#: would report a sweep result that cannot have happened. Three is the knee: it costs one
#: roster against a bench of one and buys back nothing above it.
#:
#: ⚠ **This is a different game and the report says so.** The auto-sub engine covers with
#: what the bench holds, so three reserves substitute differently from twelve — which is
#: part of what the model is being graded on. What the gate measures is the model's edge
#: *at a three-man bench*; carrying that conclusion to a twelve-man one is an assumption,
#: not a result.
GATE_BENCH = 3

#: The backtest's own budget (A12). Far smaller than a live plan's, and it has to be: the
#: graded seasons are 148 rosters x 33 giornate x 2, and a live-sized plan takes minutes.
#: The report states it, because a gate that graded a cheaper model than the one that ships
#: is a gate whose answer is about something else.
BACKTEST_BUDGET = Budget(
    k=3, lambdas=(-0.125, 0.0, 0.125), node_budget=600, m=2, work=60_000,
    bench_depth=4, bench_width=6,
)


@dataclass(frozen=True, slots=True)
class SeasonData:
    """One season, read once and sliced per giornata."""

    season: str
    rows: tuple[HistoryAppearance, ...]
    fixtures: tuple[Fixture, ...]
    valuations: Mapping[int, Valuation]
    #: 2026/27's role codes, for every player the replay may field (A16(3)).
    role_codes: Mapping[int, tuple[str, ...]]

    def giornate(self) -> tuple[int, ...]:
        return tuple(sorted({f.giornata for f in self.fixtures}))


def read_season(history: LineupHistory, season: str, *, roles_season: str = ROLES_SEASON) -> SeasonData:
    """Everything one season's replay reads, in one pass.

    The whole season is read and sliced per giornata rather than re-queried: 38 queries a
    season against one is not the point, the point is that one read cannot disagree with
    another about what happened.

    **Roles come from `roles_season` and `qi`/club from the replayed one** (A16(3)). The
    platform froze its Mantra tags in late July 2026 and no earlier season's are recorded,
    so the replay uses today's tags on yesterday's form — a stated limitation, printed in
    the report header, and not something the corpus can fix.
    """
    fixtures = tuple(history.fixtures(season))
    valuations = history.valuations(season)
    role_codes = history.roles(roles_season)
    rows = tuple(
        history.appearances(sorted(valuations), seasons=[season], before=date(9999, 1, 1))
    )
    return SeasonData(season=season, rows=rows, fixtures=fixtures,
                      valuations=valuations, role_codes=role_codes)


@dataclass(frozen=True, slots=True)
class GiornataFit:
    """The model, refit from everything before one giornata. Shared by every roster.

    The projection half is **absent during the burn-in**. Giornate 1 to 5 are baseline-only
    (A16(6)), and at giornata 1 there is no history at all — `project` refuses an empty one,
    correctly, and asking it anyway would turn "the model does not run yet" into a crash.
    """

    giornata: int
    cutoff: date
    targets: Mapping[int, Target]
    #: The baseline arm's own `mu` and `p` (A16(5)). Always present.
    baseline_mu: Mapping[int, float]
    baseline_p: Mapping[int, float]
    #: Every player's lega-scored vote at this giornata. Absence is its complement.
    votes: Mapping[int, float]
    #: The model half. `None` through the burn-in.
    projections: Mapping[int, Projection] | None = None
    p: Mapping[int, float] | None = None
    dependence: object | None = None


def fit_giornata(
    data: SeasonData, giornata: int, *, rules: ScoringRules, half_life: float,
    weights: PresenceWeights = PresenceWeights(),
    model: bool = True,
) -> GiornataFit:
    """Refit everything from data strictly before `giornata`. Pure given `data`.

    The cutoff is the day the giornata **opened**, not its giornata number: a postponed
    match keeps its number and is played weeks later, so a `giornata <` cut lets a result
    from the future into a fit that must not see it.

    `model=False` fits the baseline half only, which is what the burn-in needs and all it
    can have: at giornata 1 there is no history, and `project` refuses an empty one.
    """
    cutoff = first_match_date(data.fixtures, season=data.season, giornata=giornata)
    past = before(data.rows, cutoff)
    valuations = {data.season: data.valuations}
    targets = _targets(data)
    history_rows = observations(past, rules=rules, valuations=valuations)
    votes_before: dict[int, list[float]] = defaultdict(list)
    for observed in history_rows:
        votes_before[observed.player_id].append(observed.score)
    mu, p = baseline_inputs(
        {pid: votes_before.get(pid, []) for pid in targets},
        _played_latest(past, data, targets, cutoff=cutoff),
    )
    base = GiornataFit(
        giornata=giornata,
        cutoff=cutoff,
        targets=targets,
        baseline_mu=mu,
        baseline_p=p,
        votes=_votes_at(data, giornata, rules=rules),
    )
    if not model:
        return base

    projections = project(
        history_rows, targets, as_of=cutoff, config=ProjectionConfig(half_life)
    )
    classic = _classic_roles(past, targets)
    presences = presence(
        windows(past, list(data.fixtures), valuations, targets, cutoff=cutoff, k=weights.k),
        classic,
        {},  # news ablated: there are no recorded readings for a replayed season
        as_of=cutoff,
        weights=weights,
    )
    return replace(
        base,
        projections=projections,
        p={pid: presences[pid].p for pid in targets if pid in presences},
        dependence=fit_dependence(residuals(history_rows, projections, targets)),
    )


def _targets(data: SeasonData) -> dict[int, Target]:
    """Every player eligible in this season: a `quotazioni` row for it, and a role."""
    out: dict[int, Target] = {}
    for pid, valuation in data.valuations.items():
        codes = data.role_codes.get(pid)
        if not codes:
            continue
        out[pid] = Target(
            player_id=pid,
            macro=macro_role(";".join(codes), "mantra"),
            qi=valuation.qi,
            club=valuation.squadra,
        )
    return out


def _played_latest(
    past: Sequence[HistoryAppearance],
    data: SeasonData,
    targets: Mapping[int, Target],
    *,
    cutoff: date,
) -> dict[int, bool]:
    """A16(5)'s `p`: did he take a vote in **his team's** latest match before the cutoff?

    His team's, not his own. A player who last appeared in October and whose club has played
    six times since is a `p = 0`, and reading it off *his* last appearance would make him a
    1 — which is the difference between a baseline that benches an absentee and one that
    fields him every week. His club comes from the replayed season's `quotazioni`, the same
    place `presence.windows` takes it.
    """
    latest_for: dict[str, Fixture] = {}
    for fixture in sorted(
        (f for f in data.fixtures if f.played_on < cutoff), key=lambda f: f.played_on
    ):
        for club in (code_for(fixture.home), code_for(fixture.away)):
            latest_for[club] = fixture
    appeared = {(row.player_id, row.fixture) for row in past}
    out: dict[int, bool] = {}
    for pid, target in targets.items():
        latest = latest_for.get(target.club or "")
        out[pid] = latest is not None and (pid, latest) in appeared
    return out


def _votes_at(data: SeasonData, giornata: int, *, rules: ScoringRules) -> dict[int, float]:
    """Every player's lega-scored fantavoto at this giornata. Absence is its complement."""
    from fantabot.domain.lineup.scoring import lega_fantavoto

    out: dict[int, float] = {}
    for row in data.rows:
        if row.fixture.giornata != giornata or row.fixture.season != data.season:
            continue
        out[row.player_id] = lega_fantavoto(
            row.scored, rules=rules, season=data.season, role=row.role
        )
    return out


@dataclass(frozen=True, slots=True)
class ReplaySettings:
    """Everything the replay needs that is not data."""

    rules: ScoringRules
    sub_mode: SubMode
    modules: tuple[str, ...]
    bench_size: int
    half_life: float = DEFAULT_HALF_LIFE_DAYS
    budget: Budget = BACKTEST_BUDGET
    max_subs: int | None = None
    #: Giornate the model arm plans. The baseline arm always plays from 1.
    first_model: int = FIRST_MODEL_GIORNATA
    last: int = LAST_GIORNATA
    #: Rooms to replay, the corpus's own order truncated. 0 is all of them — and on the real
    #: corpus that is 148 rosters x 33 giornate x a plan each, so a smoke run says a number.
    rooms: int = 0
    #: The season whose Mantra role tags every replay uses (A16(3)). A parameter so the
    #: leak battery can move it and watch a test go red.
    roles_season: str = ROLES_SEASON


@dataclass(frozen=True, slots=True)
class SeasonResult:
    """One season, replayed."""

    season: str
    paired: tuple[Paired, ...]
    #: Rosters skipped, by reason — a roster the season cannot field is not a zero.
    skipped: Mapping[str, int]
    rooms: int
    rosters: int


def replay_season(
    data: SeasonData,
    corpus: Corpus,
    settings: ReplaySettings,
    *,
    reporter: Reporter,
    should_stop: Callable[[], bool] | None = None,
) -> SeasonResult:
    """Both arms over one season. The model arm starts at `first_model`; the baseline runs
    from giornata 1 so the opponent has a burn-in (A16(6)).

    The fit is per **giornata**, shared by every roster, which is the difference between 33
    fits a season and 4,884 — and, more importantly, the difference between every roster at
    a giornata being planned against one model and each against its own.
    """
    rooms = corpus.rooms[: settings.rooms] if settings.rooms else corpus.rooms
    skipped: Counter[str] = Counter()
    paired: list[Paired] = []
    baseline_scores: dict[str, list[float]] = defaultdict(list)
    available = set(data.giornate())

    for giornata in range(1, settings.last + 1):
        if giornata not in available:
            skipped["giornata not recorded"] += 1
            continue
        if should_stop is not None and should_stop():
            reporter.print(f"[yellow]{data.season}: stopped at g{giornata}[/yellow]")
            break
        modelling = giornata >= settings.first_model
        fit = fit_giornata(
            data, giornata, rules=settings.rules, half_life=settings.half_life,
            model=modelling,
        )
        for room in rooms:
            base: dict[str, Fielded] = {}
            model: dict[str, Fielded] = {}
            opponent = _opponent_for(baseline_scores[room.asta_id])
            for roster in room.rosters:
                arms = _replay_roster(
                    roster.player_ids, fit, data, settings,
                    modelling=modelling, opponent=opponent, skipped=skipped,
                )
                if arms is None:
                    continue
                base[roster.buyer_team_id] = arms[0]
                if arms[1] is not None:
                    model[roster.buyer_team_id] = arms[1]
            if not base:
                continue
            standings = table(base)
            baseline_scores[room.asta_id].extend(s.fantapunti for s in standings)
            if modelling and len(model) == len(base):
                paired.extend(
                    pair(room.asta_id, giornata, standings, table(model))
                )
            elif modelling:
                skipped["a roster the model arm could not plan"] += 1
        reporter.print(
            f"[dim]{data.season} g{giornata}: {len(paired)} paired row(s)[/dim]"
        )

    return SeasonResult(
        season=data.season,
        paired=tuple(paired),
        skipped=dict(skipped),
        rooms=len(rooms),
        rosters=sum(len(room.rosters) for room in rooms),
    )


def _opponent_for(scores: Sequence[float]) -> Opponent | None:
    """The room's own baseline scores so far, smoothed — or `None` while there are too few.

    A replay cannot ask the platform what the opponent scored, and a made-up opponent would
    make the league points a made-up statistic. Before the burn-in has filled there is no
    opponent and the model ranks on fantapunti, which is exactly what `choose_plan` does
    live in the same situation.
    """
    try:
        return fit_opponent(list(scores))
    except LineupError:
        return None


def _replay_roster(
    roster: Sequence[int],
    fit: GiornataFit,
    data: SeasonData,
    settings: ReplaySettings,
    *,
    modelling: bool,
    opponent: Opponent | None,
    skipped: Counter[str],
) -> tuple[Fielded, Fielded | None] | None:
    """One roster's giornata under both arms, or `None` when the season cannot field it.

    **The plan's seed is derived from the giornata and the roster, not threaded from a
    caller's generator.** A threaded one would make each plan depend on how many plans ran
    before it, so replaying one room alone — which is exactly what a smoke run does — would
    give different XIs from replaying it inside the corpus, and the two could not be
    compared. AD6's rule, applied to the one place a backtest can break it.
    """
    projected = fit.projections if fit.projections is not None else fit.targets
    eligible = [pid for pid in roster if pid in fit.targets and pid in projected]
    if len(eligible) < 11 + settings.bench_size:
        skipped["too few eligible players"] += 1
        return None
    roles_by_id = {pid: list(data.role_codes.get(pid, ())) for pid in eligible}
    base_value = sub_aware(
        {pid: fit.baseline_mu.get(pid, 0.0) for pid in eligible},
        {pid: fit.baseline_p.get(pid, 0.0) for pid in eligible},
    )
    inputs = LineupInputs(
        roster_ids=eligible,
        roles_by_id=roles_by_id,
        fvmma_by_id=base_value,
        modules=list(settings.modules),
        competition=0,
        mday=fit.giornata,
        cmday=fit.giornata,
        tid=0,
        bench_size=settings.bench_size,
    )
    try:
        baseline_plan = plan_lineups(inputs)[0]
    except LineupError:
        skipped["the baseline arm could not field a module"] += 1
        return None
    fielded_base = field(
        module=baseline_plan.module,
        starts=baseline_plan.starts,
        bench=baseline_plan.bench,
        votes=fit.votes,
        roles={pid: normalize_roles(roles_by_id[pid]) for pid in eligible},
        rules=settings.rules,
        sub_mode=settings.sub_mode,
        modules=settings.modules,
        max_subs=settings.max_subs,
    )
    if not modelling or fit.projections is None or fit.p is None:
        return fielded_base, None

    projections, presences = fit.projections, fit.p
    try:
        players = assemble_roster(
            eligible, roles_by_id=roles_by_id, fvmma_by_id=base_value,
            normalize=normalize_roles,
        )
        model_value = sub_aware(
            {pid: projections[pid].mu for pid in eligible},
            {pid: presences.get(pid, 0.0) for pid in eligible},
        )
        chosen = choose_plan(
            PlanInputs(
                roster=tuple(players),
                modules=settings.modules,
                mu={pid: projections[pid].mu for pid in eligible},
                p={pid: presences.get(pid, 0.0) for pid in eligible},
                sigma_tilde={pid: projections[pid].sigma_tilde2 ** 0.5 for pid in eligible},
                macro={pid: fit.targets[pid].macro for pid in eligible},
                club={pid: fit.targets[pid].club for pid in eligible},
                dependence=fit.dependence,  # type: ignore[arg-type]
                rules=settings.rules,
                bench_size=settings.bench_size,
                value=model_value,
                max_subs=settings.max_subs,
            ),
            rng=np.random.default_rng(seed_for(0, fit.giornata, roster_seed(eligible))),
            budget=settings.budget,
            sub_mode=settings.sub_mode,
            opponent=opponent,
            should_stop=None,
        )
    except (LineupError, ValueError):
        skipped["the model arm could not field a module"] += 1
        return fielded_base, None
    fielded_model = field(
        module=chosen.module,
        starts=chosen.starts,
        bench=chosen.bench,
        votes=fit.votes,
        roles={pid: normalize_roles(roles_by_id[pid]) for pid in eligible},
        rules=settings.rules,
        sub_mode=settings.sub_mode,
        modules=settings.modules,
        max_subs=settings.max_subs,
    )
    return fielded_base, fielded_model


def roster_seed(roster: Sequence[int]) -> int:
    """A roster's stable identity, for the seed. `hash()` is salted per process (AD6)."""
    import hashlib

    digest = hashlib.sha256(",".join(str(pid) for pid in sorted(roster)).encode()).digest()
    return int.from_bytes(digest[:4], "big")


@dataclass(frozen=True, slots=True)
class SeasonVerdict:
    """One graded season's answer."""

    season: str
    points: Interval
    fantapunti: Interval
    rows: int

    @property
    def guard_holds(self) -> bool:
        """The non-regression guard: the model may lose a little fantapunti, not a lot."""
        return bool(self.fantapunti.mean >= FANTAPUNTI_GUARD)

    @property
    def passes(self) -> bool:
        return bool(self.points.positive) and self.guard_holds


@dataclass(frozen=True, slots=True)
class GateReport:
    """Gate 1's whole answer, and everything a reader needs to judge it."""

    half_life: float
    sweep_season: str
    sweep: tuple[tuple[float, float], ...]
    verdicts: tuple[SeasonVerdict, ...]
    budget: Budget
    sub_mode: SubMode
    roles_season: str
    rooms: int
    rosters: int
    #: The bench the replay ran at. Printed because it is **not** the lega's, and the
    #: difference is part of what was graded — see `GATE_BENCH`.
    bench: int
    skipped: Mapping[str, int]

    @property
    def passes(self) -> bool:
        """**Both** graded seasons, or it does not pass (A16). One season is a coin that
        came up heads."""
        return bool(self.verdicts) and all(bool(v.passes) for v in self.verdicts)

    def lines(self) -> list[str]:
        """The report, markup-free. Every number a reader needs to disbelieve it."""
        out = [
            f"Gate 1: {'PASS' if self.passes else 'FAIL'}",
            f"  H = {self.half_life:.0f} days, swept on {self.sweep_season} "
            f"({', '.join(f'{h:.0f}d {d:+.4f}' for h, d in self.sweep)})",
            f"  sub mode {self.sub_mode} (confirmed, passed explicitly)",
            f"  corpus {self.rooms} room(s), {self.rosters} roster(s); "
            f"roles from {self.roles_season}; bench {self.bench}",
            f"  budget k={self.budget.k} lambdas={len(self.budget.lambdas)} "
            f"m={self.budget.m} work={self.budget.work}",
        ]
        for verdict in self.verdicts:
            out.append(
                f"  {verdict.season}: d points {verdict.points.mean:+.4f} "
                f"[{verdict.points.low:+.4f}, {verdict.points.high:+.4f}] "
                f"over {verdict.points.rooms}x{verdict.points.giornate} clusters, "
                f"{verdict.rows} row(s)"
            )
            out.append(
                f"    d fantapunti {verdict.fantapunti.mean:+.3f} "
                f"[{verdict.fantapunti.low:+.3f}, {verdict.fantapunti.high:+.3f}] "
                f"- guard {'holds' if verdict.guard_holds else 'BREACHED'} "
                f"(floor {FANTAPUNTI_GUARD:+.1f})"
            )
        for reason, count in sorted(self.skipped.items()):
            out.append(f"  skipped {count}: {reason}")
        out.append(
            "  limitation: roles are the platform's frozen 2026/27 tags, applied to earlier"
            " seasons; no earlier season's tags are recorded anywhere we can read."
        )
        if self.bench != GATE_BENCH:
            out.append(f"  limitation: replayed at bench {self.bench}, not the gate's own"
                       f" {GATE_BENCH}.")
        out.append(
            f"  limitation: the bench is {self.bench}, not the lega's 12. At 12 the sweep"
            " season fields no roster at all (A16(2) eligibility against a 2026/27 rosa),"
            " so what is graded is the model's edge at a short bench."
        )
        return out


def sweep_half_life(
    data: SeasonData,
    corpus: Corpus,
    settings: ReplaySettings,
    *,
    candidates: Sequence[float] = SWEEP_HALF_LIVES,
    reporter: Reporter,
) -> tuple[float, tuple[tuple[float, float], ...]]:
    """Pick H on the sweep season, and never on gated data (A16).

    The argmax of mean Δ league points over the sweep season alone. Ties go to the **first**
    candidate, which is the shortest half-life: between two H that the sweep cannot separate,
    the one that forgets faster is the one less likely to be fitting a season that is over.
    """
    scored: list[tuple[float, float]] = []
    for half_life in candidates:
        result = replay_season(
            data, corpus, replace(settings, half_life=half_life), reporter=reporter
        )
        delta = (
            sum(row.delta_points for row in result.paired) / len(result.paired)
            if result.paired
            else float("-inf")
        )
        scored.append((half_life, delta))
        reporter.print(f"[dim]sweep {half_life:.0f}d: {delta:+.4f}[/dim]")
    best = max(scored, key=lambda item: (item[1], -item[0]))[0]
    return best, tuple(scored)


def grade(
    result: SeasonResult, *, rng: np.random.Generator, draws: int = 2_000
) -> SeasonVerdict:
    """One graded season's two intervals, over the same rows."""
    return SeasonVerdict(
        season=result.season,
        points=two_way_bootstrap(result.paired, rng=rng, draws=draws, value="delta_points"),
        fantapunti=two_way_bootstrap(
            result.paired, rng=rng, draws=draws, value="delta_fantapunti"
        ),
        rows=len(result.paired),
    )


def run_gate(
    history: LineupHistory,
    corpus: Corpus,
    settings: ReplaySettings,
    *,
    seasons: Sequence[str],
    sweep_season: str,
    seed: int,
    reporter: Reporter,
    draws: int = 2_000,
    should_stop: Callable[[], bool] | None = None,
) -> GateReport:
    """The whole gate: sweep H on one season, grade the others with it, report.

    The sweep season is replayed first and its argmax is carried into the graded seasons
    **unchanged** (A16). A half-life tuned on 2024/25 and then graded on 2024/25 would be a
    model choosing its own exam, and the test that proves the gated arm's H came from the
    sweep is the one that keeps it that way.

    `seed` is an `int` and the `Generator` is built here, not handed in: the interface must
    not import numpy at all (AD3), and a command body that constructed one would put it on
    an import path the hourly job can reach.
    """
    rng = np.random.default_rng(seed)
    sweep_data = read_season(history, sweep_season, roles_season=settings.roles_season)
    half_life, sweep = sweep_half_life(sweep_data, corpus, settings, reporter=reporter)
    reporter.print(f"[dim]H = {half_life:.0f} days, from {sweep_season}[/dim]")

    verdicts: list[SeasonVerdict] = []
    skipped: Counter[str] = Counter()
    rooms = rosters = 0
    for season in seasons:
        data = read_season(history, season, roles_season=settings.roles_season)
        result = replay_season(
            data, corpus, replace(settings, half_life=half_life),
            reporter=reporter, should_stop=should_stop,
        )
        skipped.update(result.skipped)
        rooms, rosters = result.rooms, result.rosters
        if not result.paired:
            reporter.print(f"[yellow]{season}: no paired rows; it cannot be graded[/yellow]")
            continue
        verdicts.append(grade(result, rng=rng, draws=draws))

    return GateReport(
        half_life=half_life,
        sweep_season=sweep_season,
        sweep=sweep,
        verdicts=tuple(verdicts),
        budget=settings.budget,
        sub_mode=settings.sub_mode,
        roles_season=settings.roles_season,
        rooms=rooms,
        rosters=rosters,
        bench=settings.bench_size,
        skipped=dict(skipped),
    )
