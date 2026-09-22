"""P(a player gets a vote this matchday): his recent team matches, then the news. Pure.

The projection answers what he scores *if* he plays; this answers whether he does. Two
parts (SPEC "The model", A10, A17(4), A24):

**History, `p_hist`.** A beta-binomial over the player's last K team matches before the
cutoff: `votes` of `matches`, where a team match is one his club played. The window crosses
into last season through *that* season's club, so a player who moved in the summer reads
his old club's matches, not his new one's. A row in a match his club did not play is
dropped rather than counted (A9's unresolvable 2.2%). The Beta prior is fitted by moments
per Classic role across the population — keepers vote far more steadily than forwards —
and a role the moments cannot fit (one player, or no spread to measure) borrows the pool
of every role. The estimator allows each player his own number of matches; with an equal
number it is the ANOVA one, `rho = (n·V/(μ(1-μ)) - 1)/(n - 1)`, and the prior's
concentration is `1/rho - 1`. rho ≤ 0 means no spread beyond binomial noise, so everyone is
the mean; rho ≥ 1 means the prior carries no weight.

**News.** A reading's weight is `w = aged_confidence` (the asta's, A10), and:

* `tv = cal(t) = t + max(0, d - t)·q_role` puts titolarita on the vote scale: he votes if
  he starts, or if he is available, does not start and comes off the bench (`q_role`).
  A17(4) had `(1 - t)·q_role` there, which gave a fresh, confident injury (d = 0, t = 0) a
  vote probability of q_role — 0.55 for a midfielder; A24 keeps the bench to the
  available share. At d = 1 the two agree.
* `d_eff = 1 - w·(1 - d)` lets availability decay with the reading.
* `p = (1 - w)·d_eff·p_hist + w·tv`: with no reading, or a silent one, `p = p_hist`;
  a fresh, certain one is `tv` alone; a stale one decays back to `p_hist`.

The weights are declared priors, not fitted: the news history only starts on 2026-08-28.
No clock — `as_of` is a parameter.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from fantabot.domain.asta.sentiment import SentimentWeights, aged_confidence
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation, plays_in
from fantabot.domain.shared.values import SentimentRow

#: The pool of every role, for a role the moments cannot fit.
POOLED = "*"
CLASSIC_ROLES = frozenset({"P", "D", "C", "A"})


@dataclass(frozen=True, slots=True)
class PresenceWeights:
    """The declared priors."""

    #: Team matches in `p_hist`'s window.
    k: int = 6
    #: P(vote | available, not starting) per Classic role: A17(4)'s `q_role`.
    q_role: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({"A": 0.6, "C": 0.55, "D": 0.35, "P": 0.15})
    )
    #: A reading's half-life in days — the asta's, so both trust one row alike.
    half_life_days: float = SentimentWeights().half_life_days

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError(f"k must be at least one team match, got {self.k}")
        if set(self.q_role) != CLASSIC_ROLES:
            raise ValueError(
                f"q_role must name exactly {sorted(CLASSIC_ROLES)}, got {sorted(self.q_role)}"
            )
        for role, q in self.q_role.items():
            if not 0.0 <= q <= 1.0:
                raise ValueError(f"q_role[{role!r}] is a probability; got {q}")
        if math.isnan(self.half_life_days) or self.half_life_days <= 0:
            raise ValueError(f"half_life_days must be > 0, got {self.half_life_days}")


@dataclass(frozen=True, slots=True)
class Window:
    """A player's last team matches before the cutoff, and the votes he got in them."""

    votes: int
    matches: int


@dataclass(frozen=True, slots=True)
class BetaPrior:
    mean: float
    #: α + β: how many team matches the prior is worth. ∞ is "everyone is the mean".
    concentration: float


@dataclass(frozen=True, slots=True)
class Presence:
    player_id: int
    p_hist: float
    #: The news reading's aged confidence; 0 with no reading.
    news_weight: float
    p: float


def windows(
    appearances: Iterable[HistoryAppearance],
    fixtures: Iterable[Fixture],
    valuations: Mapping[str, Mapping[int, Valuation]],
    player_ids: Iterable[int],
    *,
    cutoff: date,
    k: int,
) -> dict[int, Window]:
    """Each player's last `k` team matches strictly before `cutoff`, newest first, each
    season's through his club that season, and how many of them he has a row in."""
    past = sorted(
        (f for f in fixtures if f.played_on < cutoff), key=lambda f: f.played_on, reverse=True
    )
    played: dict[int, set[Fixture]] = defaultdict(set)
    for row in appearances:
        played[row.player_id].add(row.fixture)
    clubs: dict[tuple[str, str], list[Fixture]] = {}

    def team_matches(season: str, club: str) -> list[Fixture]:
        if (season, club) not in clubs:
            clubs[season, club] = [f for f in past if f.season == season and plays_in(f, club)]
        return clubs[season, club]

    out: dict[int, Window] = {}
    for pid in player_ids:
        mine = [
            f
            for season, by_player in valuations.items()
            if (valuation := by_player.get(pid)) is not None
            for f in team_matches(season, valuation.squadra)
        ]
        mine.sort(key=lambda f: f.played_on, reverse=True)
        last = mine[:k]
        out[pid] = Window(votes=sum(f in played[pid] for f in last), matches=len(last))
    return out


def fit_priors(windows: Mapping[int, Window], roles: Mapping[int, str]) -> dict[str, BetaPrior]:
    """A Beta prior per role, by moments over the players in `roles`, and the pool of every
    role under `POOLED`. Raises when not even the pool can be fitted."""
    by_role: dict[str, list[Window]] = defaultdict(list)
    for pid, role in roles.items():
        by_role[role].append(windows.get(pid, Window(0, 0)))
    pooled = _moments([w for group in by_role.values() for w in group])
    if pooled is None:
        raise ValueError(
            "history too thin to fit p_hist's prior: it needs two players, some spread of "
            "vote rates and a player with two team matches"
        )
    out = {POOLED: pooled}
    for role, group in by_role.items():
        out[role] = _moments(group) or pooled
    return out


def _moments(group: Iterable[Window]) -> BetaPrior | None:
    """The beta-binomial moment estimator over players with unequal matches, or `None` when
    the group cannot identify it."""
    seen = [w for w in group if w.matches > 0]
    players = len(seen)
    total = sum(w.matches for w in seen)
    if total == 0:
        return None
    mean = sum(w.votes for w in seen) / total
    if not 0.0 < mean < 1.0:
        return None
    # E[Σ n(r - μ)²] = μ(1-μ)·[(m - 1) + rho·(N - Σn²/N - m + 1)].
    spread = sum(w.matches * (w.votes / w.matches - mean) ** 2 for w in seen)
    # 0 for one player, or for any number with one match each: nothing then separates a
    # player's own rate from binomial noise.
    identifying = total - sum(w.matches**2 for w in seen) / total - players + 1
    if identifying <= 0:
        return None
    rho = (spread / (mean * (1.0 - mean)) - (players - 1)) / identifying
    if rho <= 0:
        return BetaPrior(mean=mean, concentration=math.inf)
    if rho >= 1:
        return BetaPrior(mean=mean, concentration=0.0)
    return BetaPrior(mean=mean, concentration=1.0 / rho - 1.0)


def p_hist(window: Window, prior: BetaPrior) -> float:
    """The posterior mean of his vote rate. No team matches is exactly the prior mean."""
    if window.matches == 0 or math.isinf(prior.concentration):
        return prior.mean
    return (prior.concentration * prior.mean + window.votes) / (
        prior.concentration + window.matches
    )


def cal(t: float, *, d: float, q: float) -> float:
    """titolarita on the vote scale: starts, or is available and comes off the bench (A24)."""
    return t + max(0.0, d - t) * q


def presence(
    windows: Mapping[int, Window],
    roles: Mapping[int, str],
    sentiment: Mapping[int, SentimentRow],
    *,
    as_of: date,
    weights: PresenceWeights = PresenceWeights(),
) -> dict[int, Presence]:
    """p for every player in `roles`, which is also the population the prior is fitted on.

    `sentiment` is keyed by player id as the port returns it; a reading must carry that
    id's `str`, or one player's news would silently become another's.
    """
    priors = fit_priors(windows, roles)
    aging = SentimentWeights(half_life_days=weights.half_life_days)
    out: dict[int, Presence] = {}
    for pid, role in roles.items():
        q = weights.q_role[role]
        base = p_hist(windows.get(pid, Window(0, 0)), priors[role])
        row = sentiment.get(pid)
        if row is None:
            out[pid] = Presence(player_id=pid, p_hist=base, news_weight=0.0, p=base)
            continue
        if row.player_id != str(pid):
            raise ValueError(f"the reading for player {pid} is player {row.player_id!r}'s")
        w = aged_confidence(row, as_of=as_of, weights=aging)
        d_eff = 1.0 - w * (1.0 - row.disponibilita)
        tv = cal(row.titolarita, d=row.disponibilita, q=q)
        out[pid] = Presence(
            player_id=pid, p_hist=base, news_weight=w, p=(1.0 - w) * d_eff * base + w * tv
        )
    return out
