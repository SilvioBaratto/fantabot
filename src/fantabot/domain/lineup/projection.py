"""What a player is expected to score if he plays: normal-normal empirical Bayes. Pure.

For each player, the projection gives μ (expected lega fantavoto *given a vote*), `sigma2`
(the spread of one appearance) and `sigma_tilde2 = sigma2 + v`, the predictive spread that
also carries the uncertainty v about μ itself (SPEC "The model", A17). Presence — whether
he gets a vote at all — is `presence.py`'s question, not this one's.

**Weights.** An appearance Δd days before the cutoff weighs `w = 2^(-Δd/H)`, H in days.
Two counts follow from them, and SPEC A23 (amending A17) says which is which:

* `n = Σw` is how much each player's rows *weigh*: the prior's WLS weights, the within-
  player moments and the sigma2 shrink.
* `n_eff = (Σw)²/Σw²`, the Kish count, is how well his weighted mean ȳ is *known*:
  Var(ȳ) = s²/n_eff, not s²/Σw. It is what the τ² moment, the posterior and v use. A17
  used Σw here too, and on the lega's data that over-subtracted the noise in ȳ until τ²
  was exactly 0 for every H ≤ 360 days — every μ its prior, history ignored.

At H = ∞ every weight is 1 and the two counts agree. H has no default — the backtest
sweeps it and a silent default would grade a value nobody chose.

**The prior mean** is a weighted least-squares fit across players of each appearance's
score on the player's macro role, the `qi` of *that appearance's own season* (preseason,
so leak-free — SPEC A9) and a team effect. Only rows whose club resolved to a side of the
match take part (A9's 2.2% are dropped from the fit, not from the player's mean). The team
effect is **effect-coded**: effects average to 0 across clubs, so a club with no rows — a
promoted one — contributes nothing and gets the average. A player with no current `qi`
gets his role's weighted mean `qi`, and no club gets no team effect (stats-1).

**Variances, by weighted moments.** s² is pooled within-player variance per macro role
(a role with no within-player spread borrows the pool of all roles). τ² is the
between-player variance of the level around the prior, floored at 0 — at 0 everyone is
their prior mean. sigma2 shrinks s² toward the player's own variance with `sigma_prior_weight`
pseudo-appearances.

**The posterior.** `μ = (n_eff τ²ȳ + s²m)/(n_eff τ² + s²)` and `v = τ²s²/(n_eff τ² + s²)`,
computed in shrinkage form. With no history that is exactly the prior mean and
sigma_tilde2 = s² + τ²; with a long one, the player's own mean.

No clock: the cutoff `as_of` is a parameter, and history on or after it raises — the date
filter is `history.before`'s, and this is the backstop against a caller that skipped it.
numpy only here, reached lazily from the projection branch (`test_lineup_imports.py`).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt

from fantabot.domain.lineup.history import HistoryAppearance, Valuation, side
from fantabot.domain.lineup.scoring import ScoringRules, lega_fantavoto

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class ProjectionConfig:
    #: H, the half-life of an appearance's weight, in days. `math.inf` is unweighted.
    half_life_days: float
    #: Pseudo-appearances of the role's s² in a player's own sigma2.
    sigma_prior_weight: float = 10.0

    def __post_init__(self) -> None:
        if math.isnan(self.half_life_days) or self.half_life_days <= 0:
            raise ValueError(f"half_life_days must be > 0, got {self.half_life_days}")
        if math.isnan(self.sigma_prior_weight) or self.sigma_prior_weight < 0:
            raise ValueError(f"sigma_prior_weight must be >= 0, got {self.sigma_prior_weight}")


@dataclass(frozen=True, slots=True)
class Observation:
    """One scored appearance, as the fit reads it."""

    player_id: int
    played_on: date
    #: The lega's fantavoto for the appearance.
    score: float
    #: `qi` of the appearance's own season; `None` with no `quotazioni` row that season.
    qi: int | None
    #: The player's club when his side of the match resolved; `None` otherwise.
    club: str | None


@dataclass(frozen=True, slots=True)
class Target:
    """A player to project, as he stands at the cutoff."""

    player_id: int
    #: Macro role (`domain.asta.roles.macro_role`, Mantra system).
    macro: str
    qi: int | None
    club: str | None


@dataclass(frozen=True, slots=True)
class Projection:
    player_id: int
    prior_mean: float
    #: Σw: how much the player's rows weigh.
    n: float
    #: (Σw)²/Σw²: how well his weighted mean is known (SPEC A23).
    n_eff: float
    mu: float
    #: The role-pooled within-player variance this player's posterior used.
    s2: float
    tau2: float
    #: One appearance's variance: s² shrunk toward the player's own.
    sigma2: float
    #: Posterior variance of μ.
    v: float

    @property
    def sigma_tilde2(self) -> float:
        """Predictive variance of one appearance: sigma2 + v."""
        return self.sigma2 + self.v


def observations(
    appearances: Iterable[HistoryAppearance],
    *,
    rules: ScoringRules,
    valuations: Mapping[str, Mapping[int, Valuation]],
) -> list[Observation]:
    """History rows scored the lega's way, each with its own season's `qi`, and a club only
    when the player's side of the match resolves (`history.side`)."""
    out: list[Observation] = []
    for row in appearances:
        season = row.fixture.season
        valuation = valuations.get(season, {}).get(row.player_id)
        resolved = valuation is not None and side(row.fixture, valuation.squadra) is not None
        out.append(
            Observation(
                player_id=row.player_id,
                played_on=row.fixture.played_on,
                score=lega_fantavoto(row.scored, rules=rules, season=season, role=row.role),
                qi=None if valuation is None else valuation.qi,
                club=valuation.squadra if resolved and valuation is not None else None,
            )
        )
    return out


def weights(dates: Sequence[date], *, as_of: date, half_life_days: float) -> Floats:
    """`2^(-Δd/H)` for each date, Δd in days before `as_of`."""
    ages = np.array([(as_of - d).days for d in dates], dtype=np.float64)
    if math.isinf(half_life_days):
        return np.ones_like(ages)
    return np.power(2.0, -ages / half_life_days)


def project(
    history: Sequence[Observation],
    players: Mapping[int, Target],
    *,
    as_of: date,
    config: ProjectionConfig,
) -> dict[int, Projection]:
    """A projection for every player in `players`, fitted on `history`.

    `history` is the population the prior and the variances are fitted on, not only the
    players projected; an observation of a player not in `players` has no macro role and
    is ignored. Raises on history dated on or after `as_of`, and on history too thin to
    give any within-player variance.
    """
    rows = [o for o in history if o.player_id in players]
    late = [o for o in rows if o.played_on >= as_of]
    if late:
        raise ValueError(
            f"{len(late)} observation(s) not before {as_of}, e.g. player {late[0].player_id} "
            f"on {late[0].played_on}: history must end before the cutoff"
        )
    if not rows:
        raise ValueError("no history to fit the projection on")

    w = weights([o.played_on for o in rows], as_of=as_of, half_life_days=config.half_life_days)
    y = np.array([o.score for o in rows], dtype=np.float64)
    stats = _player_stats(rows, w, y)
    s2_by_role = _within_variance(stats, players)
    prior = _Prior.fit(rows, w, y, players)
    tau2 = _between_variance(stats, players, prior, rows, w, s2_by_role)

    result: dict[int, Projection] = {}
    for pid, target in players.items():
        m = prior.mean(target)
        s2 = s2_by_role.get(target.macro, s2_by_role["*"])
        n, ybar, own_ss, w2 = stats.get(pid, (0.0, 0.0, 0.0, 0.0))
        n_eff = n * n / w2 if w2 > 0 else 0.0
        # The shrinkage form of the same posterior: B is the weight on the player's own
        # mean. At n = 0 it is exactly 0, so μ is exactly m and v exactly τ² — the ratio
        # form `s²m / s²` is off by a unit in the last place.
        shrink = n_eff * tau2 / (n_eff * tau2 + s2)
        k = config.sigma_prior_weight
        result[pid] = Projection(
            player_id=pid,
            prior_mean=m,
            n=n,
            n_eff=n_eff,
            mu=m + shrink * (ybar - m),
            s2=s2,
            tau2=tau2,
            sigma2=s2 + (own_ss - n * s2) / (k + n) if k + n > 0 else s2,
            v=tau2 * (1.0 - shrink),
        )
    return result


def _player_stats(
    rows: Sequence[Observation], w: Floats, y: Floats
) -> dict[int, tuple[float, float, float, float]]:
    """Per player: n = Σw, the weighted mean ȳ, the weighted sum of squares about it, Σw²."""
    index: dict[int, list[int]] = defaultdict(list)
    for i, o in enumerate(rows):
        index[o.player_id].append(i)
    stats: dict[int, tuple[float, float, float, float]] = {}
    for pid, idx in index.items():
        wi, yi = w[idx], y[idx]
        n = float(wi.sum())
        ybar = float((wi * yi).sum() / n)
        ss = float((wi * (yi - ybar) ** 2).sum())
        stats[pid] = (n, ybar, ss, float((wi**2).sum()))
    return stats


def _within_variance(
    stats: Mapping[int, tuple[float, float, float, float]], players: Mapping[int, Target]
) -> dict[str, float]:
    """s² per macro role, and the pool of all roles under `"*"`.

    Reliability-weighted: each player contributes `n - Σw²/n` degrees of freedom, which is
    `count - 1` when every weight is 1. A role with no within-player spread takes the pool.
    """
    num: dict[str, float] = defaultdict(float)
    dof: dict[str, float] = defaultdict(float)
    for pid, (n, _, ss, w2) in stats.items():
        role = players[pid].macro
        num[role] += ss
        dof[role] += n - w2 / n
    total_num, total_dof = sum(num.values()), sum(dof.values())
    if total_num <= 0 or total_dof <= 0:
        raise ValueError("history too thin: no player has two appearances to vary between")
    pooled = total_num / total_dof
    out = {"*": pooled}
    for role in num:
        out[role] = num[role] / dof[role] if num[role] > 0 and dof[role] > 0 else pooled
    return out


class _Prior:
    """The WLS fit of score on (macro role, qi, effect-coded team)."""

    def __init__(
        self,
        roles: list[str],
        teams: list[str],
        beta: Floats,
        mean_qi: Mapping[str, float],
        fallback: float,
    ) -> None:
        self.roles = roles
        self.teams = teams
        self.beta = beta
        self.mean_qi = mean_qi
        self.fallback = fallback

    @classmethod
    def fit(
        cls,
        rows: Sequence[Observation],
        w: Floats,
        y: Floats,
        players: Mapping[int, Target],
    ) -> _Prior:
        fit = [i for i, o in enumerate(rows) if o.qi is not None and o.club is not None]
        fallback = float((w * y).sum() / w.sum())
        if not fit:
            return cls([], [], np.zeros(0), {}, fallback)
        roles = sorted({players[rows[i].player_id].macro for i in fit})
        teams = sorted({club for i in fit if (club := rows[i].club) is not None})
        x = np.array([cls._features(players[rows[i].player_id].macro, float(rows[i].qi or 0),
                                    rows[i].club, roles, teams) for i in fit])
        root = np.sqrt(w[fit])
        beta, *_ = np.linalg.lstsq(x * root[:, None], y[fit] * root, rcond=None)
        mean_qi: dict[str, float] = {}
        for role in roles:
            idx = [i for i in fit if players[rows[i].player_id].macro == role]
            mean_qi[role] = float(
                np.average([float(rows[i].qi or 0) for i in idx], weights=w[idx])
            )
        return cls(roles, teams, beta, mean_qi, fallback)

    @staticmethod
    def _features(
        role: str, qi: float, club: str | None, roles: list[str], teams: list[str]
    ) -> list[float]:
        """Role one-hots, qi, then K-1 effect-coded team columns: team k < K is a 1 in
        column k, the last team is -1 in every column, and an unknown club is all 0."""
        row = [1.0 if role == r else 0.0 for r in roles] + [qi]
        coded = [0.0] * max(len(teams) - 1, 0)
        if club in teams:
            k = teams.index(club)
            if k < len(teams) - 1:
                coded[k] = 1.0
            else:
                coded = [-1.0] * len(coded)
        return row + coded

    def mean(self, target: Target) -> float:
        if target.macro not in self.roles:
            return self.fallback
        qi = float(target.qi) if target.qi is not None else self.mean_qi[target.macro]
        return float(
            np.dot(self._features(target.macro, qi, target.club, self.roles, self.teams),
                   self.beta)
        )

    def fitted(self, observation: Observation, target: Target) -> float:
        """The prior's value for one past appearance, at that appearance's own qi."""
        return self.mean(
            Target(target.player_id, target.macro, observation.qi, observation.club)
        )


def _between_variance(
    stats: Mapping[int, tuple[float, float, float, float]],
    players: Mapping[int, Target],
    prior: _Prior,
    rows: Sequence[Observation],
    w: Floats,
    s2_by_role: Mapping[str, float],
) -> float:
    """τ², by moments: E[(ȳ - m̄)²] = τ² + s²/n_eff, averaged over players weighted by
    n_eff (how well each ȳ is known), floored at 0.

    `m̄` is the prior at the player's *own* past appearances — each at its season's `qi`
    and club — so a level measured last season is compared with last season's prior, not
    with this season's.
    """
    fitted_sum: dict[int, float] = defaultdict(float)
    for i, o in enumerate(rows):
        fitted_sum[o.player_id] += float(w[i]) * prior.fitted(o, players[o.player_id])
    excess = total = 0.0
    for pid, (n, ybar, _, w2) in stats.items():
        s2 = s2_by_role.get(players[pid].macro, s2_by_role["*"])
        n_eff = n * n / w2
        excess += n_eff * (ybar - fitted_sum[pid] / n) ** 2 - s2
        total += n_eff
    return max(0.0, excess / total)
