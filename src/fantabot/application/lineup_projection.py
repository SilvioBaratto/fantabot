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

**The opponent adjustment is opt-in and additive.** With `FANTABOT_LINEUP_OPPONENT` on, a
roster player's μ moves by `beta_role * (x_opponent - centre_role)` before the sub-aware
term, so the XI, the bench and E[pts] all see it (`domain/lineup/matchup`, SPEC A26). The
setting is read where it is used and fails closed; off, μ is the projection's own number to
the last bit. Anything in the adjustment that fails costs the adjustment and never the plan.

**The platform's own "out" outranks the model.** A roster player whose `lineUpInfo` row says
`status == 2` gets p = 0, after the history and the news have blended it (SPEC A27): the news is
fetched weekly and a reading older than the injury still says he plays. What `status` 2 means
is **observed, not documented** — four players, `percent` 0, a return date — so only that value
is read: `percent` is not a probability and no other `status` was ever measured. Three things
follow from that, and with nobody out the outcome is the one it was, field for field.

* **He is left out of every plan, at one point.** p = 0 prices him as absent, which is not the
  same as not fielding him: his value is the replacement level `v`, so the matcher took him
  over a healthy man valued below `v`. So the matcher and the evaluator are handed the roster
  *minus* him (`_match`), and the XI, the fallback list and every bench are built without him.
  His line stays in the table at p = 0.
* **A lineup still goes out.** If the roster cannot field a legal XI and the bench the lega
  asks for without them, they stay in the roster, still at p = 0, and the note says why. All or
  nothing, on purpose: the fail-safe is the behaviour before the exclusion, not a second
  optimizer searching for the smallest set to bring back.
* **A tripwire guards the premise.** The meaning being observed, a field that starts flagging
  more than a quarter of the roster has probably changed it (`domain/lineup/ruled_out`). Then
  none of this runs — no p = 0, nobody left out — and the note says so.

**Staleness is a verdict, not a gate.** It says whether the voti behind μ are final (T25); the
fallback that acts on it is `--shadow`'s, in phase 6. A lineup with no matchday coordinates
yet has nothing to be fresh *for*, and says that rather than reading as fresh.

numpy and scipy live behind this module (`domain/lineup/projection`), so the default path must
never import it. `tests/domain/lineup/test_lineup_imports.py` declares the one edge that may
reach it, from `interface/lineup.py`'s command body.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from fantabot.application.containment import contained
from fantabot.application.lineup_planner import plan_lineups
from fantabot.application.lineup_submit import PROJECTION, Projected
from fantabot.application.scrape import current_season
from fantabot.domain.asta.roles import macro_role, normalize_roles
from fantabot.domain.lineup import matchup as matchup_model
from fantabot.domain.lineup import positional
from fantabot.domain.lineup.choose import (
    Budget,
    ChosenPlan,
    PlanInputs,
    choose_plan,
    seed_for,
)
from fantabot.domain.lineup.dependence import fit as fit_dependence
from fantabot.domain.lineup.dependence import residuals
from fantabot.domain.lineup.errors import (
    BenchIncomplete,
    LineupError,
    NoFieldableModule,
    OpponentUnavailable,
)
from fantabot.domain.lineup.freshness import Freshness, staleness
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, LineupHistory, Valuation
from fantabot.domain.lineup.models import PlannedLineup, assemble_roster
from fantabot.domain.lineup.opponent import Opponent
from fantabot.domain.lineup.opponent import fit as fit_opponent
from fantabot.domain.lineup.presence import PresenceWeights, presence, windows
from fantabot.domain.lineup.projection import (
    Observation,
    Projection,
    ProjectionConfig,
    Target,
    observations,
    project,
)
from fantabot.domain.lineup.ruled_out import TRIPWIRE_SHARE, implausible
from fantabot.domain.lineup.value import replacement_level, sub_aware

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from datetime import date

    from fantabot.adapters.files.lineup_runs import LineupShadow
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_planner import LineupInputs
    from fantabot.domain.lineup.scoring import ScoringRules
    from fantabot.domain.lineup.substitution import SubMode

#: Seasons of history the projection reads, the current one included.
SEASONS_READ = 5
#: H, in days: what the Gate 1 sweep picked on 2023/24 (90d +0.0268, 180d +0.0234, 365d
#: +0.0223) and the H at which Gate 1 then passed on 2024/25 and 2025/26. A live default
#: other than the graded one would submit a model nobody graded.
DEFAULT_HALF_LIFE_DAYS = 90.0
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
    #: The evaluated plan (T30), or `None` when the chain could not produce one. `plans[0]`
    #: is then the fallback — the sub-aware matcher's own answer, which needs no opponent,
    #: no draws and no budget.
    chosen: ChosenPlan | None = None
    #: How the opponent was obtained, or the named reason there is none. Printed, so an
    #: operator reading "E[pts]" knows whether there was a league behind it.
    opponent: str = ""
    #: The substitution mode the chain ran under, and whether the operator set it. An
    #: unset mode is **assumed**, never defaulted silently: the three field different XIs
    #: and Open Question 1 is still open (`domain/lineup/substitution.parse_sub_mode`).
    sub_mode: SubMode = "basic"
    sub_mode_assumed: bool = True
    #: Why there is no `chosen`, when there is none.
    fallback: str = ""
    #: Who was priced against which opponent, by how much. Empty when the opponent adjustment
    #: is off, and when it is on but could not price anyone.
    adjustments: Mapping[int, matchup_model.Adjustment] = field(default_factory=dict)
    #: One sentence on the opponent adjustment: what it did, or why it did nothing. `""` when
    #: the setting is off, which is how a reader (and a test) can tell off from skipped.
    matchup_note: str = ""
    #: One sentence on what the platform's `status == 2` did: who it priced at p = 0 and why,
    #: whether the plans leave them out or could not, or that the tripwire skipped it all.
    #: `""` when nobody is flagged, which is how a reader tells a guard that found no one
    #: from one that was never there.
    ruled_out_note: str = ""


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
    sub_mode: SubMode | None = None,
    voti_refreshed: bool = False,
    refreshed: Callable[[int], bool] | None = None,
    config: ProjectionConfig | None = None,
    weights: PresenceWeights = PresenceWeights(),
    should_stop: Callable[[], bool] | None = None,
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
            league_id=league_id,
            sub_mode=sub_mode,
            max_subs=substitution_cap(calculate),
            voti_refreshed=voti_refreshed,
            refreshed=refreshed,
            config=config,
            weights=weights,
            should_stop=should_stop,
        )
    return ProjectionReport(
        baseline=tuple(plan_lineups(inputs)), projection=outcome, names=names, competition=comp
    )


def substitution_cap(calculate: Mapping[str, Any]) -> int | None:
    """`settings/calculate.subst.ssnum`, if it is a cap at all.

    Open Question 1: the field reads 5 for this lega and nothing has confirmed it *is* the
    substitution allowance rather than, say, the number of declared switches. A missing or
    non-positive value is read as **uncapped** rather than as zero — an engine told it may
    make no substitutions fields a man short every week, which is a worse wrong answer than
    an engine that makes one too many.
    """
    subst = calculate.get("subst")
    raw = subst.get("ssnum") if isinstance(subst, Mapping) else None
    try:
        cap = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return cap if cap > 0 else None


def plan_projection(
    inputs: LineupInputs,
    history: LineupHistory,
    *,
    as_of: date,
    rules: ScoringRules,
    league_id: int = 0,
    sub_mode: SubMode | None = None,
    max_subs: int | None = None,
    budget: Budget | None = None,
    voti_refreshed: bool = False,
    refreshed: Callable[[int], bool] | None = None,
    config: ProjectionConfig | None = None,
    weights: PresenceWeights = PresenceWeights(),
    should_stop: Callable[[], bool] | None = None,
    matchup_on: bool | None = None,
) -> ProjectionOutcome:
    """Project the roster, rank the modules on it, and evaluate the shortlist. Reads
    history, opens nothing.

    `sub_mode` is `None` when the operator has not set it (AD4): the chain still runs, under
    BASIC, and the outcome records that it was **assumed**. `should_stop` is the wall
    clock's one seam into the pure chain — the clock itself never reaches here. `matchup_on`
    is `None` unless a caller forces the opponent adjustment on or off: the setting is then
    read here, at the point of use (`matchup_enabled`).
    """
    season = current_season(as_of)
    seasons = _seasons(season)
    valuations = {s: history.valuations(s) for s in seasons}
    targets = _targets(inputs, history.roles(season), valuations.get(season, {}))
    rows = history.appearances(sorted(targets), seasons=seasons, before=as_of)
    fixtures = {s: history.fixtures(s) for s in seasons}

    history_rows = observations(rows, rules=rules, valuations=valuations)
    projections = project(
        history_rows,
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
    ruling = _ruling(inputs, roster)
    # After the history and the news have blended it, and the only place the roster's p is
    # built: the news is fetched weekly and a reading older than the injury still says he
    # plays, while the lineup DTO is read at submit time. Every consumer below — the values,
    # the matcher, the evaluator, the table and the shadow record — takes this one number.
    p = {pid: 0.0 if pid in ruling.ruled else presences[pid].p for pid in roster}
    matchups = _matchups(
        inputs,
        enabled=matchup_enabled(matchup_on),
        # Paired here, drained inside the containment: a length mismatch is the adjustment's
        # failure, and the adjustment is never worth the plan.
        scored=zip(rows, (o.score for o in history_rows), strict=True),
        valuations=valuations, fixtures=fixtures, targets=targets, roster=roster,
        season=season, as_of=as_of,
    )
    adjustments = matchups.adjustments
    # Before the sub-aware term, not after: `p·μ + (1-p)·v` is what the matcher ranks the
    # modules on, and the evaluator is handed the same μ, so a man facing a strong side is
    # worth less to the XI, to the bench order and to E[pts] alike.
    mu = {pid: mu[pid] + adjustments[pid].delta if pid in adjustments else mu[pid] for pid in mu}
    values = sub_aware(mu, p)
    matched = _match(inputs, values, ruling.ruled)
    plans = matched.plans
    # The evaluator is handed the men the matcher was: a plan it chose cannot name a man the
    # matcher's own list was built without.
    playable = [pid for pid in roster if pid not in matched.left_out]

    mode: SubMode = sub_mode or "basic"
    opponent, opponent_note = _opponent(history, league_id=league_id)
    chosen, fallback = _chosen(
        inputs,
        roster=playable,
        targets=targets,
        projections=projections,
        mu=mu,
        p=p,
        values=values,
        history_rows=history_rows,
        rules=rules,
        sub_mode=mode,
        opponent=opponent,
        league_id=league_id,
        max_subs=max_subs,
        budget=budget,
        should_stop=should_stop,
    )
    return ProjectionOutcome(
        plans=_ranked(plans, chosen, inputs),
        lines=_lines(roster, targets, classic, projections, mu, p, values),
        freshness=_freshness(
            fixtures[season],
            cmday=inputs.cmday,
            # The marker is asked **here**, where `cmday` is finally known: "were the voti
            # refreshed" is a question about one matchday, and A20 makes the answer half of
            # whether the history is fresh at all. It was hard-coded `False` until
            # 2026-09-23, which made every shadowed run report itself stale whatever the
            # refresh had actually done.
            refreshed=voti_refreshed if refreshed is None else refreshed(inputs.cmday),
        ),
        replacement=replacement_level({pid: p[pid] * mu[pid] for pid in roster}),
        as_of=as_of,
        seasons=seasons,
        chosen=chosen,
        opponent=opponent_note,
        sub_mode=mode,
        sub_mode_assumed=sub_mode is None,
        fallback=fallback,
        adjustments=adjustments,
        matchup_note=matchups.note,
        ruled_out_note=_ruled_out_note(inputs, roster, ruling, matched),
    )


@dataclass(frozen=True, slots=True)
class _Ruling:
    """What the platform's `status == 2` does to this run, decided once."""

    #: The roster players forced to p = 0. Empty when nobody is flagged, and when the tripwire
    #: fired: a skipped guard forces nobody.
    ruled: frozenset[int] = frozenset()
    #: The tripwire's sentence, `""` when it did not fire.
    tripped: str = ""


@dataclass(frozen=True, slots=True)
class _Matched:
    """The matcher's plans, and which ruled-out men they were built without."""

    plans: tuple[PlannedLineup, ...]
    #: The men kept out of every plan. Empty when nobody was ruled out and when they could
    #: not be kept out.
    left_out: frozenset[int] = frozenset()
    #: Why somebody ruled out is still in the roster, `""` when nobody is.
    forced: str = ""


def _ruling(inputs: LineupInputs, roster: Sequence[int]) -> _Ruling:
    """Who the platform marked out among the men the projection prices, unless that many of
    them cannot be injured at once.

    Counted over `roster`, the men with a line, and not over the DTO: an id the projection
    never priced is neither forced, nor left out, nor part of the share the tripwire reads.
    """
    flagged = frozenset(pid for pid in roster if pid in inputs.ruled_out)
    if implausible(len(flagged), len(roster)):
        return _Ruling(
            tripped=(
                f"lineUpInfo status 2 on {len(flagged)} of {len(roster)} roster players, more "
                f"than {TRIPWIRE_SHARE:.0%}: its meaning is observed, not documented, so it "
                "has probably changed. NOT applied: nobody is set to p = 0 or kept out of "
                "the lineup"
            )
        )
    return _Ruling(ruled=flagged)


def _match(inputs: LineupInputs, values: Mapping[int, float], ruled: frozenset[int]) -> _Matched:
    """The matcher's plans on the roster **minus** the ruled-out men.

    The one point where they leave, and the only one: the plans' XI, their fallback list and
    their bench order all come out of this call, and `plan_projection` hands the evaluator the
    same roster. Nothing downstream filters them a second time.

    Fail-safe: when the roster cannot field a module, or fill the bench, without them, the
    plans are built with them and `forced` says why. A refusal here would field nothing, and
    the hourly job's contract is that a lineup goes out. If it cannot go out with them either,
    that refusal propagates, as it did before the guard existed.
    """
    priced = replace(inputs, fvmma_by_id=values)
    if not ruled:
        return _Matched(tuple(plan_lineups(priced)))
    without = replace(priced, roster_ids=[pid for pid in inputs.roster_ids if pid not in ruled])
    try:
        return _Matched(tuple(plan_lineups(without)), left_out=ruled)
    except (NoFieldableModule, BenchIncomplete) as exc:
        return _Matched(tuple(plan_lineups(priced)), forced=_why_not(exc))


def _why_not(exc: NoFieldableModule | BenchIncomplete) -> str:
    """The one clause of the note that says what was missing without the ruled-out men."""
    if isinstance(exc, NoFieldableModule):
        return "no allowed module can be fielded"
    return f"the bench cannot be filled ({exc})"


_RULED_OUT = "ruled out by the platform (lineUpInfo status 2, observed to mean out, not documented)"


def _ruled_out_note(
    inputs: LineupInputs, roster: Sequence[int], ruling: _Ruling, matched: _Matched
) -> str:
    """What `status` 2 did, in one sentence, or `""` when it found no one.

    Players are named in roster order, whatever order a set iterates in, so a record reads the
    same twice. `roster` is the men the projection has a line for, so an id it never priced is
    not named: the note says what the model did, not what the DTO said.
    """
    if ruling.tripped:
        return ruling.tripped
    named = [inputs.ruled_out_notes.get(pid, str(pid)) for pid in roster if pid in ruling.ruled]
    if not named:
        return ""
    who = "; ".join(named)
    if matched.forced:
        return f"{_RULED_OUT}, p = 0 but NOT kept out, because without them {matched.forced}: {who}"
    return f"{_RULED_OUT}, p = 0 and kept out of the XI and the bench: {who}"


def matchup_enabled(override: bool | None = None) -> bool:
    """`FANTABOT_LINEUP_OPPONENT` **now**, parsed at the point of use and failing closed.

    Only an explicit yes turns the opponent adjustment on: `1`, `true`, `yes` or `on`, in any
    case. Unset, empty, a typo and `off` all leave it off, because the setting changes the
    number the hourly job's XI is chosen on and an operator who mistyped it has not asked for
    that. Re-read on every call, like `FANTABOT_LINEUP_NEWS`, so it can be flipped between two
    hourly runs without restarting anything.

    Args:
        override: A caller that already knows (a test, a replay) passes the answer; it beats
            the setting.
    """
    if override is not None:
        return override
    from fantabot.config import LINEUP_OPPONENT_VAR, live_flag

    return live_flag(LINEUP_OPPONENT_VAR)


_MATCHUP_ON = "opponent adjustment on"


def _matchups(
    inputs: LineupInputs,
    *,
    enabled: bool,
    scored: Iterable[tuple[HistoryAppearance, float]],
    valuations: Mapping[str, Mapping[int, Valuation]],
    fixtures: Mapping[str, Sequence[Fixture]],
    targets: Mapping[int, Target],
    roster: Sequence[int],
    season: str,
    as_of: date,
) -> matchup_model.Matchups:
    """The roster's opponent adjustments, with the sentence for the operator as the note.
    Empty and without a note when the setting is off, which is how a reader tells off from
    skipped.

    **Contained.** The adjustment is an extra on top of a plan that is already worth having,
    and the hourly job must not lose a matchday to it: any exception inside is a plan without
    it, named by exception *type* only (`contained`'s rule — a driver's message carries the
    connection string). A Classic lega is skipped by name: the slopes were measured on the
    five Mantra macro roles and nothing says they carry over.

    `scored` is drained inside that boundary, so a lazy pairing of the history with its scores
    fails as the adjustment and not as the plan. `roster` is the men the projection has a line
    for, and bounds what is priced and counted.
    """
    if not enabled:
        return matchup_model.Matchups(adjustments={}, note="")
    if inputs.fmt == "classic":
        return _skipped("classic is not what it was measured on")
    wanted = set(roster)
    paired = {pid: pairing for pid, pairing in inputs.pairings.items() if pid in wanted}

    def run() -> matchup_model.Matchups:
        return matchup_model.adjust(
            scored=list(scored),
            valuations=valuations,
            fixtures=fixtures,
            macro={pid: target.macro for pid, target in targets.items()},
            pairings=paired,
            season=season,
            cmday=inputs.cmday,
            as_of=as_of,
        )

    result, failure = contained(run)
    if result is None:
        return _skipped(failure)
    if not result.adjustments:
        return _skipped(result.note)
    unpaired = len(wanted) - len(paired)
    tail = f"; {unpaired} roster player(s) without a pairing" if unpaired else ""
    return matchup_model.Matchups(
        adjustments=result.adjustments, note=f"{_MATCHUP_ON}: {result.note}{tail}"
    )


def _skipped(why: str) -> matchup_model.Matchups:
    """No adjustments, and the operator's sentence saying the setting was on and why it did
    nothing."""
    return matchup_model.Matchups(adjustments={}, note=f"{_MATCHUP_ON}, skipped: {why}")


def _ranked(
    plans: Sequence[PlannedLineup], chosen: ChosenPlan | None, inputs: LineupInputs
) -> tuple[PlannedLineup, ...]:
    """The evaluated XI first, then the matcher's own list as the fallback walk.

    **`plans[0]` is the plan, wherever it came from.** The submit path walks this list and
    falls to the next module when the platform refuses one, so putting the chosen XI at the
    head is what makes the model's answer the one that is *sent* — and building a
    `PlannedLineup` in the interface instead would be a decision the app also needs, which
    is the `GET /asta/plan` mistake in miniature.

    The matcher's own answer is kept behind it, deduplicated on `(module, starts)`: a
    refused schema must still have somewhere to fall to, and the fallback that needs no
    opponent and no draws is exactly the list that was already there.
    """
    if chosen is None:
        return tuple(plans)
    head = PlannedLineup(
        module=chosen.module,
        starts=chosen.starts,
        bench=chosen.bench,
        competition=inputs.competition,
        mday=inputs.mday,
        cmday=inputs.cmday,
        tid=inputs.tid,
        guard=positional.refusal(
            chosen.module,
            [normalize_roles(inputs.roles_by_id.get(pid, ())) for pid in chosen.starts],
        ),
    )
    rest = [p for p in plans if (p.module, p.starts) != (head.module, head.starts)]
    return (head, *rest)


def _opponent(history: LineupHistory, *, league_id: int) -> tuple[Opponent | None, str]:
    """The opponent's score distribution, or the named reason there is none.

    A **named degradation, never a crash.** `OpponentUnavailable` is the ordinary state of
    a lega three rounds into a season — a KDE over four scores is a guess with a probability
    attached — and the plan is still worth having without one: it ranks on E[fantapunti]
    instead and says so. What it must not do is quietly report an E[pts] computed against an
    opponent nobody fitted.
    """
    scores = history.calculated_scores(league_id)
    try:
        return fit_opponent(scores), f"{len(scores)} calculated round(s)"
    except OpponentUnavailable as exc:
        return None, f"none ({exc})"


def _chosen(
    inputs: LineupInputs,
    *,
    roster: Sequence[int],
    targets: Mapping[int, Target],
    projections: Mapping[int, Projection],
    mu: Mapping[int, float],
    p: Mapping[int, float],
    values: Mapping[int, float],
    history_rows: Sequence[Observation],
    rules: ScoringRules,
    sub_mode: SubMode,
    opponent: Opponent | None,
    league_id: int,
    max_subs: int | None,
    budget: Budget | None,
    should_stop: Callable[[], bool] | None,
) -> tuple[ChosenPlan | None, str]:
    """The evaluated plan, or `None` and why. The chain's one call site in the application.

    **Mantra only, and it says so.** The substitution engine reads `schema.admissions`,
    which is the 11 Mantra schemi; a Classic lega has one role per player and a different
    legality, so it falls back by name rather than being run through a matcher that would
    answer a question nobody asked.

    Everything else that can go wrong is a `LineupError` or a `ValueError` from a history
    too thin to fit the copula, and every one of them is a **fallback**: `plans[0]` — the
    sub-aware matcher's own answer — needs no opponent, no draws and no budget, and an
    hourly job that raised here would field nothing at all.

    ⚠ The *matcher's* own refusals are not caught here and must not be: `plan_lineups` runs
    first, and a roster that fields no module or cannot fill a bench has no fallback to fall
    to. What this contains is what the **chain** adds — the copula, the shortlist, the
    bench search — which is exactly the work that is optional.

    `roster` is the men the plan may name, which is the priced roster minus any ruled-out man
    the matcher was built without; every per-player map below is read through it, so a man
    outside it is not part of the evaluator's draws, bench pool or candidates.

    `mu` is what the evaluator draws around: the projections' own, moved by this week's
    opponent adjustment when there is one. It is not read off `projections` because the
    adjustment moves this week's μ and **not** the history the dependence is fitted on:
    `residuals(history_rows, projections, ...)` measures how past scores sat around the
    model's own expectation, and shifting that by one week's opponent would shift every
    standardized residual of that player.
    """
    if inputs.fmt == "classic":
        return None, "classic: the evaluated chain is Mantra-only"
    try:
        players = assemble_roster(
            list(roster),
            roles_by_id=inputs.roles_by_id,
            fvmma_by_id=values,
            normalize=normalize_roles,
        )
        dependence = fit_dependence(residuals(history_rows, projections, targets))
        plan_inputs = PlanInputs(
            roster=tuple(players),
            modules=tuple(inputs.modules),
            mu={pid: mu[pid] for pid in roster},
            p={pid: p[pid] for pid in roster},
            sigma_tilde={pid: math.sqrt(projections[pid].sigma_tilde2) for pid in roster},
            macro={pid: targets[pid].macro for pid in roster},
            club={pid: targets[pid].club for pid in roster},
            dependence=dependence,
            rules=rules,
            bench_size=inputs.bench_size,
            value={pid: values[pid] for pid in roster},
            max_subs=max_subs,
        )
        chosen = choose_plan(
            plan_inputs,
            rng=np.random.default_rng(
                seed_for(league_id, inputs.competition, inputs.cmday)
            ),
            budget=budget,
            sub_mode=sub_mode,
            opponent=opponent,
            should_stop=should_stop,
        )
    except (LineupError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if chosen.stopped:
        # SPEC A17(6): **the wall clock is a hard abort, and a hard abort is a fallback.**
        # `choose_plan` is right to return a plan rather than raise — a pure chain that
        # threw would have no answer to give — but a half-searched plan is a plan that
        # depends on how loaded the machine was, and submitting one would mean the same
        # lega on the same matchday fielded two different XIs on two different evenings.
        # So it is reported and dropped, and `plans[0]` goes back to the matcher's answer,
        # which needs no draws and is the same everywhere.
        return None, f"aborted: {', '.join(chosen.cuts) or 'the wall clock fired'}"
    return chosen, ""


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
    mu: Mapping[int, float],
    p: Mapping[int, float],
    values: Mapping[int, float],
) -> tuple[PlayerLine, ...]:
    lines = [
        PlayerLine(
            player_id=pid,
            macro=targets[pid].macro,
            role=classic[pid],
            mu=mu[pid],
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


def projector_for(
    store: TokenStore,
    *,
    league_id: int,
    as_of: date,
    sub_mode: SubMode | None = None,
    should_stop: Callable[[], bool] | None = None,
    for_submit: bool = False,
    voti_refreshed: Callable[[int], bool] | None = None,
) -> Callable[[int], Projected]:
    """The `--shadow` seam, bound to one lega (T36).

    **This is the one place the projection crosses into the submit path**, and it hands over
    default-path types only — plans and a `LineupShadow`. A seam typed as `ProjectionReport`
    would put this module, and through it numpy and scipy, on the import graph of the hourly
    `indexcompare` submit (AD3), and the guard counts a `TYPE_CHECKING` import exactly as it
    counts a real one.

    Everything it can fail at is a **named fallback**, never an exception: no evaluated plan,
    a stale history, an assumed sub mode, an opponent that could not be fitted. The caller
    contains it too — belt and braces, because the caller cannot know what a future failure
    here will be — but a reason a reader can act on beats a type name, and that is what this
    produces while it still knows what went wrong.
    """

    def build(competition: int) -> Projected:
        report = projection_for_league(
            store,
            league_id=league_id,
            competition=competition,
            as_of=as_of,
            sub_mode=sub_mode,
            should_stop=should_stop,
            refreshed=voti_refreshed,
        )
        return projected_from(report, for_submit=for_submit)

    return build


def projected_from(report: ProjectionReport, *, for_submit: bool = False) -> Projected:
    """A `ProjectionReport` as the submit path needs it. Pure.

    **`for_submit` is the whole safety property of the phase.** The same three conditions are
    a *warning* on a shadow run and a **fallback** on a projection submit, because they are
    reasons not to trust the plan and a shadow is not trusted with anything:

    * **stale** — the voti behind μ are not final (SPEC Freshness: the projection path is
      skipped, with the reason recorded);
    * **an assumed sub mode** — SPEC A7: an unset `FANTABOT_LINEUP_SUB_MODE` means a
      projection *submit* falls back, because the three modes field different XIs and
      nobody has said which this lega plays;
    * **no opponent** — the plan was ranked on E[fantapunti], which is a different objective
      from the one the gate graded.

    In every one of those the **shadow is still carried**, so the record shows what the
    projection would have done *and* why it was not sent. Dropping it would throw away the
    evidence at the moment it is most interesting.
    """
    outcome = report.projection
    warnings = list(outcome.freshness.warnings)
    # Carried whether it ran or was skipped: the record is where an operator learns what the
    # XI was chosen on, and a run with no word about it would read as the setting being off.
    if outcome.matchup_note:
        warnings.append(outcome.matchup_note)
    # Without it the record gives no reason why a man the history rates is not in the XI.
    if outcome.ruled_out_note:
        warnings.append(outcome.ruled_out_note)
    reasons: list[str] = []
    if not outcome.freshness.fresh:
        reasons.append(f"stale: {'; '.join(outcome.freshness.reasons)}")
    if outcome.sub_mode_assumed:
        reasons.append(
            f"sub mode assumed {outcome.sub_mode} (FANTABOT_LINEUP_SUB_MODE unset)"
        )
    if outcome.chosen is not None and outcome.chosen.objective != "points":
        reasons.append(f"no opponent ({outcome.opponent})")
    warnings.extend(reasons)

    if outcome.chosen is None:
        return Projected(plans=(), shadow=None, fallback=outcome.fallback,
                         warnings=tuple(warnings))
    shadow = shadow_of(outcome, report.names)
    if for_submit and reasons:
        return Projected(plans=(), shadow=shadow, fallback="; ".join(reasons),
                         warnings=tuple(warnings))
    return Projected(plans=outcome.plans, shadow=shadow, warnings=tuple(warnings))


def shadow_of(outcome: ProjectionOutcome, names: Mapping[int, str]) -> LineupShadow | None:
    """The one-line summary the run record carries. `None` when there is no evaluated plan.

    ⚠ **`e_pts` and `p_wdl` are zero when no opponent could be fitted**, and the first entry
    of `cuts` says so. `LineupShadow.e_pts` is a `float` and the app's row type pins it
    (T06), so there is no `None` to write; a bare 0.0 reads as a certain loss, which is why
    it never travels without the sentence that explains it. The plan was ranked on
    E[fantapunti] in that case and `e_fp` is the number that decided it.
    """
    from fantabot.adapters.files.lineup_runs import LineupShadow

    plan = outcome.chosen
    if plan is None:
        return None
    evaluation = plan.evaluation
    cuts = list(plan.cuts)
    if evaluation.points is None:
        cuts.insert(0, f"ranked on E[fp]: no opponent ({outcome.opponent})")
    if plan.stopped:  # pragma: no cover - an aborted plan is dropped before it gets here
        cuts.append("stopped")
    return LineupShadow(
        model=PROJECTION,
        module=plan.module,
        starter_ids=plan.starts,
        bench_ids=plan.bench,
        starters=tuple(names.get(pid, str(pid)) for pid in plan.starts),
        bench=tuple(names.get(pid, str(pid)) for pid in plan.bench),
        e_pts=evaluation.points or 0.0,
        p_wdl=(evaluation.win or 0.0, evaluation.drawn or 0.0, evaluation.loss or 0.0),
        e_fp=evaluation.fantapunti,
        sd=evaluation.fantapunti_sd,
        cuts=tuple(cuts),
    )
