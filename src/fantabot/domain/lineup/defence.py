"""The *modificatore difesa*: what it is worth, and how likely it is to be earned. Pure.

The operator's leghe award a bonus from the average vote of the defence, earned only when
**at least four defenders get a vote** (his rules, 2026-09-23). Each lega's own table comes
from `settings/calculate` (`domain/lineup/rules.DefenceModifier`). A back four is worth its
points cost only when the bonus it chases is both likely and large, so the planner adds

    P(four defenders vote) * E[bonus | they do]

to a back-four lineup's summed score and lets that total compete with every other module.

* `p_defenders_voting` — each starting defender plays with his `p_play`; one who does not is
  covered by a bench defender who does, up to one per absence and **within the lega's
  substitution cap**. The cap is spent on the other starters' absences first — the
  conservative reading, since the order the platform applies them in is unmeasured.
* `expected_defence_bonus` — the averaged votes are the keeper's (when the lega counts him,
  `smoddg`) and the three best defenders'. Their mean is each player's `vote_if_plays`; the
  average is modelled as Normal(mean, `VOTE_SD` / sqrt(n)) and the bonus integrated over the
  lega's bands. `VOTE_SD` is a declared prior, not a fitted one.
* `swapped_module` — the module a cross-role switch leaves (433 with D -> C is 343, as captured
  on 3677376).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence

from fantabot.domain.lineup.rules import DefenceModifier

#: Defenders needed on the pitch for the bonus.
DEFENDERS_FOR_BONUS = 4
#: How many of the best defenders' votes the average takes.
DEFENDERS_AVERAGED = 3
#: Spread of one player's plain vote around its expectation. A declared prior.
VOTE_SD = 0.75


def _count_distribution(probabilities: Sequence[float]) -> list[float]:
    """`dist[k]` = P(exactly k of these independent events happen) — a Poisson-binomial."""
    dist = [1.0]
    for p in probabilities:
        p = min(max(p, 0.0), 1.0)
        nxt = [0.0] * (len(dist) + 1)
        for k, mass in enumerate(dist):
            nxt[k] += mass * (1.0 - p)
            nxt[k + 1] += mass * p
        dist = nxt
    return dist


def p_defenders_voting(
    starters: Sequence[float],
    bench: Sequence[float],
    *,
    need: int = DEFENDERS_FOR_BONUS,
    max_subs: int | None = None,
    others: Sequence[float] = (),
) -> float:
    """P(at least `need` defenders get a vote).

    `starters`/`bench` are the defenders' play probabilities; `others` the rest of the XI's,
    whose absences use up `max_subs` first. `max_subs` None is no cap. 0 when fewer than
    `need` defenders start.
    """
    if len(starters) < need:
        return 0.0
    playing = _count_distribution(starters)
    covering = _count_distribution(bench)
    if max_subs is None:
        other_absent = [(1.0, 0)]
    else:
        other_dist = _count_distribution(others)
        n_others = len(others)
        other_absent = [(mass, n_others - k) for k, mass in enumerate(other_dist)]
    total = 0.0
    for p_o, absent_o in other_absent:
        subs_left = math.inf if max_subs is None else max(0, max_subs - absent_o)
        for k_s, p_s in enumerate(playing):
            absent = len(starters) - k_s
            for k_b, p_b in enumerate(covering):
                if k_s + min(k_b, absent, subs_left) >= need:
                    total += p_o * p_s * p_b
    return total


def averaged_votes(
    keeper_vote: float | None, defender_votes: Sequence[float], *, includes_keeper: bool
) -> list[float]:
    """The votes the modifier averages: the three best defenders', plus the keeper's when
    the lega counts him."""
    votes = sorted(defender_votes, reverse=True)[:DEFENDERS_AVERAGED]
    if includes_keeper and keeper_vote is not None:
        votes.append(keeper_vote)
    return votes


def expected_defence_bonus(
    keeper_vote: float | None,
    defender_votes: Sequence[float],
    modifier: DefenceModifier,
    *,
    vote_sd: float = VOTE_SD,
) -> tuple[float, float]:
    """`(expected average vote, E[bonus])` for these expected votes under this lega's table."""
    votes = averaged_votes(keeper_vote, defender_votes, includes_keeper=modifier.includes_keeper)
    if not votes:
        return 0.0, 0.0
    mean = sum(votes) / len(votes)
    return mean, modifier.expected(mean, vote_sd / math.sqrt(len(votes)))


def defenders_in(module: str) -> int:
    """The D count of a Classic three-digit module code."""
    return int(module[0])


def swapped_module(
    module: str, *, from_role: str, to_role: str, allowed: Collection[str]
) -> str | None:
    """The module after one `from_role` player is replaced by a `to_role` one, if the lega
    allows it (433 with D -> C is 343); None when the result is not an allowed module."""
    order = ("D", "C", "A")
    counts = dict(zip(order, (int(ch) for ch in module), strict=True))
    if counts.get(from_role, 0) < 1 or to_role not in counts:
        return None
    counts[from_role] -= 1
    counts[to_role] += 1
    code = "".join(str(counts[r]) for r in order)
    return code if code in allowed else None
