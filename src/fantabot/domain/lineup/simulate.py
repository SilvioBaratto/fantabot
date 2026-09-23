"""The evaluator: what one candidate XI is worth against the opponent. Pure, seeded, vectorised.

Three things happen per draw, and only the first is random.

* **Who played.** Presence is a Bernoulli per player at his `p` (T16), drawn once for the
  whole roster and shared by every candidate — **common random numbers**, so two XIs are
  compared on the same week rather than on two different weeks. Without it the difference
  between two candidates is dominated by which draws each happened to get.
* **What they scored.** One joint draw per player from the Gaussian copula (T17), already on
  the fantavoto scale: `dependence.draw` returns `mu + sigma_tilde * marginal`, so nothing
  here rescales it. Same bank, same reason.
* **Who was on the pitch at the end.** The auto-sub engine (T20), whose answer depends on
  the absence pattern alone — which is why the draws are **grouped** by that pattern and the
  engine is called once per distinct pattern rather than once per draw. On a 23-man board
  most draws are their own pattern, and the grouping is still what makes the arithmetic one
  vectorised sum instead of `n` Python loops.

**The opponent is analytic, never sampled** (T21). Sampling him would add variance to a
quantity we have in closed form: `goal_probabilities` integrates the fitted KDE over the
ladder's own bands exactly. The result is that two candidates differ only through *our*
draws, which is the paired comparison the shortlist is ranked on.

**The malus is a point off the team total**, one per out-of-position man. Measured on the
platform's own lines (T12): `cscr = scr + bonuses - m`, and `m` weighs exactly -1 in every
voted line of rounds 1-3.

Determinism: every random number comes from the caller's `Generator`, which
`choose.py` seeds from `(league, competition, matchday)`. Nothing here calls `default_rng`,
and the AST guard in `tests/domain/lineup/test_lineup_imports.py` is what keeps it that way —
a backtest that cannot be replayed is a gate nobody can re-run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from fantabot.domain.lineup.dependence import Member, draw

if TYPE_CHECKING:
    from fantabot.domain.lineup.dependence import Dependence
    from fantabot.domain.lineup.scoring import ScoringRules
    from fantabot.domain.lineup.substitution import SubstitutionEngine

Floats = np.ndarray[Any, np.dtype[np.float64]]
Bools = np.ndarray[Any, np.dtype[np.bool_]]


@dataclass(frozen=True, slots=True)
class DrawBank:
    """Every roster player's presence and score, for `n` draws. Shared by every candidate."""

    #: Column order. A player's column is `index[pid]`, and nothing may reorder it.
    player_ids: tuple[int, ...]
    #: `(n, R)` fantavoto, already on the platform's scale.
    scores: Floats
    #: `(n, R)` — True where the player took a vote that week.
    present: Bools

    @property
    def n(self) -> int:
        return int(self.scores.shape[0])

    @property
    def index(self) -> Mapping[int, int]:
        return {pid: i for i, pid in enumerate(self.player_ids)}

    def head(self, n: int) -> DrawBank:
        """The first `n` draws. A **prefix**, so the screen and the final round compare the
        same weeks — common random numbers across the stages, not only within one."""
        if n >= self.n:
            return self
        return DrawBank(self.player_ids, self.scores[:n], self.present[:n])


@dataclass(frozen=True, slots=True)
class Evaluation:
    """What one candidate is worth. `points` is `None` when no opponent could be fitted."""

    #: E[league points] — 3 a win, 1 a draw. `None` without an opponent.
    points: float | None
    win: float | None
    drawn: float | None
    loss: float | None
    #: E[fantapunti], and its spread across the draws.
    fantapunti: float
    fantapunti_sd: float
    #: E[goals on the lega's own ladder].
    goals: float
    #: Mean maluses taken, and mean slots left empty. Diagnostics, never a ranking key.
    malus: float
    short: float
    #: How many distinct absence patterns the draws produced — the engine's call count.
    patterns: int


def build_bank(
    members: Sequence[Member],
    player_ids: Sequence[int],
    p: Mapping[int, float],
    dependence: Dependence,
    *,
    rng: np.random.Generator,
    n: int,
) -> DrawBank:
    """One bank for the whole evaluation. `members[i]` is `player_ids[i]`.

    The two draws are taken in a fixed order — scores, then presence — so a bank is a
    function of the seed and nothing else. Swapping them would change every result while
    changing no behaviour, which is exactly the kind of difference a backtest cannot explain.
    """
    if len(members) != len(player_ids):
        raise ValueError(
            f"{len(members)} members against {len(player_ids)} players: they are one list"
        )
    if n < 1:
        raise ValueError(f"a bank is at least one draw; got {n}")
    scores = draw(members, dependence, rng=rng, n=n)
    probabilities = np.array([p.get(pid, 0.0) for pid in player_ids], dtype=np.float64)
    present = rng.random((n, len(player_ids))) < probabilities
    return DrawBank(tuple(player_ids), scores, present)


def evaluate(
    bank: DrawBank,
    *,
    engine: SubstitutionEngine,
    rules: ScoringRules,
    opponent_goals: Sequence[float] | None = None,
) -> Evaluation:
    """One candidate against the opponent, over the bank's draws.

    `engine` carries the candidate: its module, its `starts` and its bench. One engine per
    candidate, because its memo is keyed on the absence pattern *of those eleven*.
    """
    index = bank.index
    relevant = [*engine.starts, *engine.bench]
    missing = [pid for pid in relevant if pid not in index]
    if missing:
        raise ValueError(f"{missing} are not in the draw bank")

    columns = np.array([index[pid] for pid in relevant], dtype=np.intp)
    # One row per draw, one bit per relevant player: two draws share an engine answer exactly
    # when these bits agree. `unique` over the packed rows is the grouping.
    packed = np.packbits(bank.present[:, columns], axis=1)
    _rows, first, inverse = np.unique(packed, axis=0, return_index=True, return_inverse=True)

    totals = np.empty(bank.n, dtype=np.float64)
    malus = np.empty(bank.n, dtype=np.float64)
    short = np.empty(bank.n, dtype=np.float64)
    for group, representative in enumerate(first):
        voted = [pid for pid in relevant if bank.present[representative, index[pid]]]
        fielded = engine.field_xi(voted)
        members = np.array(
            [index[pid] for pid in fielded.fielded if pid is not None], dtype=np.intp
        )
        where = np.flatnonzero(inverse == group)
        # A malus is a point off the team total, one per out-of-position man (T12).
        totals[where] = bank.scores[np.ix_(where, members)].sum(axis=1) - fielded.malus
        malus[where] = fielded.malus
        short[where] = fielded.short

    goals = ladder_goals(totals, rules=rules)
    points, win, drawn, loss = _against(goals, opponent_goals)
    return Evaluation(
        points=points,
        win=win,
        drawn=drawn,
        loss=loss,
        fantapunti=float(totals.mean()),
        fantapunti_sd=float(totals.std()),
        goals=float(goals.mean()),
        malus=float(malus.mean()),
        short=float(short.mean()),
        patterns=len(first),
    )


def ladder_goals(totals: Floats, *, rules: ScoringRules) -> Floats:
    """`scoring.goals`, vectorised. Same answer, one array at a time.

    `steps` is ascending in the lega's own settings (`stgoal` reads `[6, 12, … 42]`), which
    `searchsorted` needs; it is sorted here rather than assumed, because a lega that declared
    them in another order would otherwise be scored on a silently wrong ladder.
    """
    steps = np.sort(np.asarray(rules.steps, dtype=np.float64))
    over = totals - rules.threshold
    scored = 1 + np.searchsorted(steps, over, side="right")
    return np.where(totals < rules.threshold, 0, scored).astype(np.float64)


def _against(
    goals: Floats, opponent_goals: Sequence[float] | None
) -> tuple[float | None, float | None, float | None, float | None]:
    """E[league points] and P(W/D/L), folding the analytic opponent into each draw.

    `opponent_goals[k]` is `P(opponent scores exactly k)` and the ladder cannot produce more
    goals than it has entries, so `below[g]` — the probability he scored strictly fewer than
    we did — is always in range. Without an opponent there is no result to have, and the
    caller ranks on fantapunti instead: inventing a uniform one would be a made-up league.
    """
    if opponent_goals is None:
        return None, None, None, None
    probabilities = np.asarray(opponent_goals, dtype=np.float64)
    below = np.concatenate([[0.0], np.cumsum(probabilities)])
    g = goals.astype(np.intp)
    if g.size and int(g.max()) >= probabilities.size:
        raise ValueError(
            f"the ladder reached {int(g.max())} goals and the opponent's table holds "
            f"{probabilities.size}: they were built from different `steps`"
        )
    win = below[g]
    drawn = probabilities[g]
    return (
        float((3.0 * win + drawn).mean()),
        float(win.mean()),
        float(drawn.mean()),
        float((1.0 - win - drawn).mean()),
    )
