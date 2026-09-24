"""Grade what was fielded, and what the other model would have fielded. Read-only.

Phase 7's evidence. Every scheduled run writes a record; this reads them back and, for each
matchday, recomputes both plans from the **ids** — lega-scored votes plus the auto-sub engine
under the confirmed mode — and compares the result with what the platform actually awarded.

Three claims a shadow matchday has to support, and each is one section below:

* **our arithmetic agrees with the platform's.** The recompute is checked against
  `league_fixture`'s own `points_home`/`points_away` for our `tid`. A difference over 0.01
  means the model is being graded by a scorer that does not match the one paying out, and
  nothing downstream is worth reading until it is explained.
* **the shadow would have scored X.** The same recompute over the shadow's own ids, which is
  what "the projection would have done better" has to mean if it means anything.
* **nobody took a malus.** SPEC A18, read off the platform's own `m` flag rather than
  inferred from a score a point short.

**Ids, never names.** A record carries both; grading on names would grade the wrong player
the first time two share one, and the ids are why record v2 exists.

**Records without ids are `ungradable`, said out loud.** A v1 line never carried them, and a
run that failed before it had a plan has nothing to grade. Counting those as zeros would
drag the average toward whatever the empty case scores.

The recompute reuses `domain/lineup/backtest.field` — the same function the gate replays
with. A second copy here would let the shadow report and Gate 1 disagree about what an XI
scored, which is the one thing they must never do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fantabot.adapters.files.lineup_runs import SUBMITTED, UNCONFIRMED
from fantabot.domain.lineup.backtest import Fielded, field
from fantabot.domain.lineup.scoring import lega_fantavoto

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from fantabot.adapters.files.lineup_runs import LineupRun
    from fantabot.domain.lineup.history import HistoryAppearance
    from fantabot.domain.lineup.scoring import ScoringRules
    from fantabot.domain.lineup.substitution import SubMode

#: The statuses a graded record may have. A skipped or failed run fielded nothing.
#:
#: `UNCONFIRMED` reached the platform and could not be read back — the lineup is there, so
#: it is graded like any other. The record's own caveat is about the *evidence*, not the XI.
#:
#: The two names are **imported**, not re-spelled. They are the values
#: `adapters/files/lineup_runs.py` writes into the record this module then reads back, so a
#: second copy here is a second answer to "what does a submitted run look like on disk" —
#: and the copy that would lose a rename is this one, which decides what gets graded at all.
#: `lineup_submit.py` already reads them from the same place.
GRADABLE_STATUSES = (SUBMITTED, UNCONFIRMED)

#: How far our recompute may sit from the platform's own points before it is a finding.
TOLERANCE = 0.01
#: Slack for the binary representation of a sum of halves and quarters, and for nothing
#: else. Small enough that no real difference hides under it.
_EPSILON = 1e-9
#: The most decisive goals one XI can plausibly have in one matchday. A bound, so a gap of
#: "seven bmdg" is read as what it is — a real disagreement — rather than explained away.
_MAX_DECISIVE_GOALS = 4


@dataclass(frozen=True, slots=True)
class Graded:
    """One matchday, both plans, against what the platform paid."""

    matchday: int
    serie_a_matchday: int | None
    at: str
    module: str
    #: Our recompute of the XI that was sent.
    ours: Fielded
    #: The platform's own total for our team, or `None` when the round is not calculated.
    platform: float | None
    #: The shadow's recompute, or `None` when the record carried none.
    shadow: Fielded | None
    #: What one decisive goal is worth in this lega (`bmdg`). See `decisive_goals`.
    decisive_goal: float = 1.0
    shadow_module: str = ""
    #: Starters the platform flagged with a positional malus (A18).
    malus_starters: tuple[int, ...] = ()

    @property
    def short_by(self) -> float:
        """How much the platform paid above our recompute. Negative if we paid more."""
        return 0.0 if self.platform is None else self.platform - self.ours.fantapunti

    @property
    def decisive_goals(self) -> int | None:
        """How many decisive goals would explain the gap, or `None` if none would.

        ⚠ **Our recompute is knowably short by `bmdg`, and by nothing else.** The platform
        pays +1 once to a scorer whose goal decided the match, and `match_grain` has no
        column for it — T12 measured exactly that: 147 of 153 players exact in round 1, 5
        short by `bmdg`, 1 by a malus the engine now reproduces. So a gap that is a small
        non-negative multiple of `bmdg` is **explained**, and anything else is a finding.

        Without this every matchday in which one of our eleven scored a decisive goal would
        report `MISMATCH`, and the command would cry wolf most weeks — which is how a check
        stops being read.
        """
        if self.platform is None or self.decisive_goal <= 0:
            return None
        count = self.short_by / self.decisive_goal
        nearest = round(count)
        if nearest < 0 or nearest > _MAX_DECISIVE_GOALS:
            return None
        if abs(count - nearest) * self.decisive_goal > TOLERANCE + _EPSILON:
            return None
        return int(nearest)

    @property
    def agrees(self) -> bool:
        """Whether our arithmetic matches the platform's, allowing for `bmdg`.

        ⚠ **The epsilon absorbs the binary float and nothing else.** Fantavoti are halves
        and quarters and the difference of two sums of them is not: a platform total
        exactly a hundredth away computes as `0.010000000000005`, and a bare `<= 0.01`
        calls that a mismatch. Rounding to a hundredth instead was the other way to get it
        wrong — it makes a real 0.011 agree, and 0.011 is above a hundredth.
        """
        if self.platform is None:
            return True
        if abs(self.short_by) <= TOLERANCE + _EPSILON:
            return True
        return self.decisive_goals is not None

    @property
    def delta(self) -> float | None:
        """Shadow minus sent, in fantapunti. `None` without a shadow."""
        return None if self.shadow is None else self.shadow.fantapunti - self.ours.fantapunti


@dataclass(frozen=True, slots=True)
class Ungradable:
    """A record that cannot be graded, and the reason — never a zero."""

    at: str
    reason: str


@dataclass(frozen=True, slots=True)
class ShadowReport:
    graded: tuple[Graded, ...]
    ungradable: tuple[Ungradable, ...]
    sub_mode: SubMode

    @property
    def mismatches(self) -> tuple[Graded, ...]:
        """Matchdays where our recompute and the platform's points part company."""
        return tuple(g for g in self.graded if not g.agrees)

    @property
    def maluses(self) -> tuple[Graded, ...]:
        """Matchdays where a starter took a positional malus (A18: this must be none)."""
        return tuple(g for g in self.graded if g.malus_starters)

    def lines(self) -> list[str]:
        """The report, markup-free: it is written to a terminal and pasted into a chat."""
        out = [f"shadow report: {len(self.graded)} graded, sub mode {self.sub_mode}"]
        for g in self.graded:
            platform = "not calculated" if g.platform is None else f"{g.platform:.2f}"
            goals = g.decisive_goals if g.agrees else None
            agreement = (
                "MISMATCH"
                if not g.agrees
                else f"ok (+{goals} bmdg)"
                if goals
                else "ok"
            )
            out.append(
                f"  md {g.matchday} (SA {g.serie_a_matchday}) {g.module}: "
                f"ours {g.ours.fantapunti:.2f} vs platform {platform} [{agreement}]"
            )
            if g.shadow is not None:
                delta = g.delta or 0.0
                out.append(
                    f"    shadow {g.shadow_module}: {g.shadow.fantapunti:.2f} "
                    f"({delta:+.2f})"
                )
            if g.malus_starters:
                out.append(f"    MALUS on {list(g.malus_starters)} — A18 says this is zero")
        for u in self.ungradable:
            out.append(f"  {u.at}: ungradable ({u.reason})")
        if self.mismatches:
            out.append(
                f"  {len(self.mismatches)} matchday(s) disagree with the platform by more "
                f"than {TOLERANCE}, and by something other than a decisive goal: the model "
                "is being graded by a scorer that does not match the one paying out."
            )
        return out


def latest_per_matchday(runs: Sequence[LineupRun], *, league_id: int) -> list[LineupRun]:
    """The last gradable run per matchday, in matchday order.

    **The last, not the first.** An hourly job writes a record every hour and re-submits
    while the round is open, so the XI that was actually in play at kickoff is the newest
    one — grading the first would grade a lineup that was replaced.

    **Keyed by (competition, matchday), not by matchday alone.** `LineupRun.matchday` is the
    *competition's* own numbering and a lega can hold more than one, so a second
    competition's matchday 3 would silently replace the first's — and the report would be
    one row short with nothing saying so.

    ⚠ **A record that does not name its competition is not a competition of its own.** A v1
    line never carried one, and on the operator's own log 27 of 52 gradable records are that
    shape — keyed as `None` they made matchday 4 appear twice, once as itself and once as a
    matchday nobody could identify. They fall into a named competition's bucket when one
    exists for the same matchday, and stand alone only when none does. Measured 2026-09-23.
    """
    named: dict[tuple[int, int], LineupRun] = {}
    unnamed: dict[int, LineupRun] = {}
    for run in runs:
        if run.league != league_id or run.status not in GRADABLE_STATUSES:
            continue
        if run.matchday is None:
            continue
        if run.competition:
            key = (run.competition, run.matchday)
            seen = named.get(key)
            if seen is None or run.at >= seen.at:
                named[key] = run
            continue
        older = unnamed.get(run.matchday)
        if older is None or run.at >= older.at:
            unnamed[run.matchday] = run

    claimed = {matchday for _competition, matchday in named}
    out = [named[key] for key in sorted(named)]
    out.extend(unnamed[md] for md in sorted(unnamed) if md not in claimed)
    return sorted(out, key=lambda run: (run.matchday or 0, run.competition or 0))


def grade_run(
    run: LineupRun,
    *,
    votes: Mapping[int, float],
    roles: Mapping[int, frozenset[str]],
    rules: ScoringRules,
    sub_mode: SubMode,
    modules: Sequence[str],
    platform: float | None,
    malus_starters: Sequence[int] = (),
    max_subs: int | None = None,
) -> Graded | Ungradable:
    """One record, recomputed. `Ungradable` rather than a zero when it cannot be."""
    if not run.starter_ids:
        return Ungradable(run.at, "no starter ids (a v1 record, or a run with no plan)")
    missing = [pid for pid in (*run.starter_ids, *run.bench_ids) if pid not in roles]
    if missing:
        return Ungradable(run.at, f"no Mantra role for {missing[:3]}")

    ours = field(
        module=run.module, starts=run.starter_ids, bench=run.bench_ids, votes=votes,
        roles=roles, rules=rules, sub_mode=sub_mode, modules=modules, max_subs=max_subs,
    )
    shadow_plan: Fielded | None = None
    shadow_module = ""
    if run.shadow is not None and run.shadow.starter_ids:
        shadow_module = run.shadow.module
        if all(pid in roles for pid in (*run.shadow.starter_ids, *run.shadow.bench_ids)):
            shadow_plan = field(
                module=run.shadow.module, starts=run.shadow.starter_ids,
                bench=run.shadow.bench_ids, votes=votes, roles=roles, rules=rules,
                sub_mode=sub_mode, modules=modules, max_subs=max_subs,
            )
    return Graded(
        matchday=run.matchday or 0,
        serie_a_matchday=run.serie_a_matchday,
        at=run.at,
        module=run.module,
        ours=ours,
        platform=platform,
        decisive_goal=rules.decisive_goal,
        shadow=shadow_plan,
        shadow_module=shadow_module,
        malus_starters=tuple(int(pid) for pid in malus_starters),
    )


def votes_at(
    rows: Sequence[HistoryAppearance], *, giornata: int, season: str, rules: ScoringRules
) -> dict[int, float]:
    """Every player's lega-scored fantavoto at one Serie A giornata.

    The same shape `backtest.field` wants: absence is the complement, so a player with no
    row simply is not in it and the engine substitutes for him.
    """
    return {
        row.player_id: lega_fantavoto(row.scored, rules=rules, season=season, role=row.role)
        for row in rows
        if row.fixture.giornata == giornata and row.fixture.season == season
    }


def report(
    runs: Sequence[LineupRun],
    *,
    league_id: int,
    sub_mode: SubMode,
    grade: Callable[[LineupRun], Graded | Ungradable],
) -> ShadowReport:
    """The whole report. `grade` is injected because every read one record needs — the
    votes, the platform's points, the malus flags — is a different query per matchday."""
    graded: list[Graded] = []
    ungradable: list[Ungradable] = []
    for run in latest_per_matchday(runs, league_id=league_id):
        outcome = grade(run)
        if isinstance(outcome, Graded):
            graded.append(outcome)
        else:
            ungradable.append(outcome)
    return ShadowReport(tuple(graded), tuple(ungradable), sub_mode)
