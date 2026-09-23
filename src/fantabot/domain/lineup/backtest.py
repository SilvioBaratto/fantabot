"""The replay: what an XI actually scored, and what the room's table said about it. Pure.

Gate 1 (SPEC A16) grades the model against a baseline over three recorded seasons. Two arms
plan the same rosters on the same giornate from the same history, and the difference between
them is the whole answer — so everything that is not the plan has to be identical, and this
module is where that is made true.

**The realized score is the platform's arithmetic, not the model's.** An XI is worth the sum
of its fielded men's lega-scored votes, less one point per out-of-position man, and the
ladder turns that into goals. The auto-sub engine runs on the **actual** absences of that
giornata, under the operator's confirmed mode, for both arms. A replay that scored the
submitted eleven rather than the fielded one would grade a game nobody played.

**The gate metric is all-play-all inside the room** (Decision 11). A room is 6 to 10 rosters
and a Serie A giornata is one week, so every roster meets every other and the week's luck —
which fixture you drew — cancels. It is reported **per fixture**, in [0, 3], because the
corpus pools rooms of different sizes and a 10-team room would otherwise weigh 9/7 of an
8-team one for no reason anyone chose.

**The baseline is deliberately weak and deliberately honest** (A16(5)): the mean of a
player's last five lega-scored votes *in the replayed season*, and `p = 1` exactly when he
took a vote in his team's latest match before g. It is what a manager with a spreadsheet
would do, and it is the thing the model has to beat to be worth an hour of anybody's week.

Nothing here reads a database, a clock or a network. Every giornata's data arrives already
cut to "before g", because a leak is invisible in a backtest replaying a database that
already holds the answer — which is what `scripts/leak_battery.py` exists to prove.

**Nor does anything here import numpy**, and that is a rule rather than an accident: the
replay is what `lineup shadow-report` recomputes a *submitted* lineup with, and a grading
command has no business loading scipy for a bootstrap it never calls. The bootstrap lives
in `domain/lineup/gate.py`, which only the gate imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from fantabot.domain.lineup.scoring import goals
from fantabot.domain.lineup.substitution import SubMode, substitute

if TYPE_CHECKING:
    from fantabot.domain.lineup.scoring import ScoringRules

#: Giornate the baseline arm replays before the model arm starts, so the model's opponent
#: has scores to be fitted on (A16(6)). The model arm plans 6..38.
BURN_IN = 5
#: The last votes the baseline averages (A16(5)).
BASELINE_WINDOW = 5
#: League points for a win and a draw. The platform's, not a choice.
WIN, DRAW = 3.0, 1.0


@dataclass(frozen=True, slots=True)
class Fielded:
    """What an XI actually did on the day."""

    module: str
    #: The eleven that took the field, `None` where nobody could be placed.
    fielded: tuple[int | None, ...]
    entered: tuple[int, ...]
    fantapunti: float
    goals: int
    malus: int
    short: int


@dataclass(frozen=True, slots=True)
class Standing:
    """One roster's giornata, after the room's table has been played out."""

    buyer: str
    fantapunti: float
    goals: int
    #: Mean league points over this roster's fixtures that week, in `[0, 3]`.
    points: float


def baseline_inputs(
    votes_before: Mapping[int, Sequence[float]],
    played_latest: Mapping[int, bool],
    *,
    window: int = BASELINE_WINDOW,
) -> tuple[dict[int, float], dict[int, float]]:
    """A16(5): the baseline arm's `mu` and `p`.

    `votes_before[pid]` is his lega-scored votes this season before g, **oldest first**, and
    `played_latest[pid]` whether he took one in his team's latest match before g. A player
    with no vote at all has `mu = 0` and `p = 0`: he is not "average", he is unknown, and
    ranking an unknown at the mean is how a baseline flatters itself.

    Deliberately not smoothed, not shrunk and not aged. The baseline is what a manager with
    a spreadsheet would do; a baseline with the model's machinery in it grades the machinery
    against itself.
    """
    mu = {
        pid: (sum(votes[-window:]) / len(votes[-window:]) if votes else 0.0)
        for pid, votes in votes_before.items()
    }
    p = {pid: 1.0 if played_latest.get(pid) else 0.0 for pid in votes_before}
    return mu, p


def field(
    *,
    module: str,
    starts: Sequence[int],
    bench: Sequence[int],
    votes: Mapping[int, float],
    roles: Mapping[int, frozenset[str]],
    rules: ScoringRules,
    sub_mode: SubMode,
    modules: Sequence[str] = (),
    max_subs: int | None = None,
) -> Fielded:
    """Run the giornata: substitute on the real absences, sum the eleven, read the ladder.

    `votes` is every player who took one, already lega-scored. Absence is the *complement* —
    a player is missing exactly when he is not in it — so a replay cannot disagree with the
    engine about who played, which is the one thing both arms must share.
    """
    outcome = substitute(
        module=module,
        starts=starts,
        bench=bench,
        voted=list(votes),
        roles=roles,
        mode=sub_mode,
        modules=modules,
        max_subs=max_subs,
    )
    total = sum(votes.get(pid, 0.0) for pid in outcome.fielded if pid is not None)
    total -= outcome.malus
    return Fielded(
        module=outcome.module,
        fielded=outcome.fielded,
        entered=outcome.entered,
        fantapunti=total,
        goals=goals(total, threshold=rules.threshold, steps=rules.steps),
        malus=outcome.malus,
        short=outcome.short,
    )


def table(results: Mapping[str, Fielded]) -> tuple[Standing, ...]:
    """The room's giornata, all-play-all, ordered by buyer.

    Every roster meets every other, so the week's luck cancels and the number that comes out
    is about the XI. Reported **per fixture** so a 10-team room does not outweigh an 8-team
    one in the pooled corpus.

    A room of one is a room with no fixture; its `points` is 0.0 rather than an average of
    nothing, and the corpus rules keep those out long before this.
    """
    buyers = sorted(results)
    earned = dict.fromkeys(buyers, 0.0)
    played = dict.fromkeys(buyers, 0)
    for home, away in combinations(buyers, 2):
        ours, theirs = results[home].goals, results[away].goals
        earned[home] += WIN if ours > theirs else DRAW if ours == theirs else 0.0
        earned[away] += WIN if theirs > ours else DRAW if ours == theirs else 0.0
        played[home] += 1
        played[away] += 1
    return tuple(
        Standing(
            buyer=buyer,
            fantapunti=results[buyer].fantapunti,
            goals=results[buyer].goals,
            points=earned[buyer] / played[buyer] if played[buyer] else 0.0,
        )
        for buyer in buyers
    )


@dataclass(frozen=True, slots=True)
class Paired:
    """One roster's giornata under both arms — the unit the bootstrap resamples."""

    room: str
    buyer: str
    giornata: int
    baseline_points: float
    model_points: float
    baseline_fantapunti: float
    model_fantapunti: float

    @property
    def delta_points(self) -> float:
        return self.model_points - self.baseline_points

    @property
    def delta_fantapunti(self) -> float:
        return self.model_fantapunti - self.baseline_fantapunti


def pair(
    room: str,
    giornata: int,
    baseline: Sequence[Standing],
    model: Sequence[Standing],
) -> tuple[Paired, ...]:
    """The two arms' tables, joined roster by roster. Paired on purpose.

    The same roster, the same giornata, the same absences: the only thing that differs is
    the XI, which is what the gate is about. An unpaired comparison of two means would be
    dominated by which weeks each arm happened to be measured on.
    """
    theirs = {standing.buyer: standing for standing in model}
    missing = [s.buyer for s in baseline if s.buyer not in theirs]
    if missing or len(theirs) != len(baseline):
        raise ValueError(
            f"{room} g{giornata}: the two arms fielded different rosters ({missing or 'count'})"
        )
    return tuple(
        Paired(
            room=room,
            buyer=ours.buyer,
            giornata=giornata,
            baseline_points=ours.points,
            model_points=theirs[ours.buyer].points,
            baseline_fantapunti=ours.fantapunti,
            model_fantapunti=theirs[ours.buyer].fantapunti,
        )
        for ours in baseline
    )
