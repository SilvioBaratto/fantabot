"""Each rosa player's expected fantavoto for this giornata. Pure.

    expected      = p_play * fv_if_plays                (the model's own forecast)
    blended       = (1 - w_ic) * expected + w_ic * s * indexCompare
    p_play        = percent / 100                        (x status_doubt when status != 1)
    fv_if_plays   = baseline * opponent * venue * ex_team

* **`p_play`** is the platform's own probable-starter percentage (`lineUpInfo.percent`), which
  already folds in injuries and suspensions: every injured player measured 2026-09-22 had
  `percent` 0 and `status` 2. A missing percentage is `default_percent`, not zero — absent is
  not injured.
* **`baseline`** shrinks this season's fantamedia (`fagrd`) toward a prior: last season's
  `media_fantavoto`, else a per-role default. Games played this season are not in the row, so
  they are estimated as `cmday - 1`; a `fagrd` of 0 means no graded game and weighs nothing.
* **`opponent`** reads the opponent's goals-for / goals-against per game relative to the league
  mean, from last season's fixtures: a C/A is helped by a leaky opponent, a P/D by a blunt one.
  A promoted club (no fixtures) is neutral. Clipped to `opp_clip`.
* **`ex_team`** is a small declared bump when the opponent is a club he was listed at in an
  earlier season.
* **`vote_if_plays`** is the plain vote (no bonus/malus) he is expected to get, shrunk the same
  way: this season's `agrd` toward last season's `media_voto`, else `DEFAULT_VOTE_PRIOR`. It
  feeds the *modificatore difesa*, which averages votes, not fantavoti (`defence.py`).
* **`indexCompare`** is the platform's composite forecast (0 when injured, ~17 for a top
  forward) on its own scale. It is blended in, rescaled per rosa so both terms share a mean,
  rather than thrown away: it knows things this model does not.

Every weight is a declared prior in `PredictWeights`, like `asta.sentiment.SentimentWeights`,
not a fitted one. No clock and no I/O: the caller hands in the rows and the history.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fantabot.domain.classic.roles import role_from_fcrle

#: Per-role fantamedia prior for a player with no history at all.
DEFAULT_ROLE_PRIOR: Mapping[str, float] = {"P": 5.0, "D": 5.9, "C": 6.2, "A": 6.6}
#: Plain-vote prior for a player with no history: the voting scale is centred on 6.
DEFAULT_VOTE_PRIOR = 6.0


@dataclass(frozen=True)
class PredictWeights:
    """The model's declared priors. Change them here, not at a call site."""

    #: Pseudo-games the prior counts for when shrinking this season's fantamedia.
    prior_games: float = 5.0
    #: Share of the final score taken from the platform's rescaled `indexCompare`.
    ic_weight: float = 0.3
    #: Opponent sensitivity: a factor of `1 + beta * (rel - 1)`, before clipping.
    opp_beta: float = 0.15
    opp_clip: tuple[float, float] = (0.85, 1.15)
    home: float = 1.02
    away: float = 0.98
    ex_team: float = 1.03
    #: Multiplier on `p_play` when the platform flags the player (`status` != 1).
    status_doubt: float = 0.5
    #: `p_play` when the row carries no `percent`.
    default_percent: float = 0.5
    role_prior: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_ROLE_PRIOR))


@dataclass(frozen=True)
class RowSignals:
    """What one `lineUpInfo` row says about one player this giornata."""

    pid: int
    role: str
    club: str | None
    opponent: str | None
    home: bool | None
    percent: float | None
    status: int | None
    fantamedia: float
    index_compare: float
    #: This season's plain average vote (`agrd`); 0 means no graded game.
    media_voto: float = 0.0


@dataclass(frozen=True)
class TeamRates:
    """A club's goals scored and conceded per game, relative to the league mean (1.0 = mean)."""

    attack: float
    defence_leak: float


@dataclass(frozen=True)
class Prediction:
    """The forecast for one player, with each factor kept so the CLI can explain it."""

    pid: int
    p_play: float
    fv_if_plays: float
    expected: float
    score: float
    factors: Mapping[str, float]
    #: Expected plain vote if he plays — the *modificatore difesa*'s input.
    vote_if_plays: float = DEFAULT_VOTE_PRIOR


def previous_season(stagione: str) -> str:
    """`"2026/27"` -> `"2025/26"`. Raises `ValueError` on any other shape."""
    start, end = stagione.split("/")
    return f"{int(start) - 1}/{int(end) - 1:02d}"


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def signals_from_row(row: Mapping[str, Any]) -> RowSignals:
    """One live Classic `lineUpInfo` row into `RowSignals`.

    `hoaw` 0 is home and 1 is away, so the player's club is `teamH` or `teamA` accordingly and
    the opponent is the other (measured 2026-09-22: Malen, Roma, `hoaw` 1, `COM`-`ROM`).
    """
    code = row.get("fcrle")
    if code is None and row.get("role"):
        code = row["role"][0]
    hoaw = row.get("hoaw")
    home_code, away_code = row.get("teamH"), row.get("teamA")
    club = opponent = None
    home: bool | None = None
    if hoaw in (0, 1) and home_code and away_code:
        home = hoaw == 0
        club, opponent = (home_code, away_code) if home else (away_code, home_code)
    status = row.get("status")
    return RowSignals(
        pid=int(row["pid"]),
        role=role_from_fcrle(code),
        club=club,
        opponent=opponent,
        home=home,
        percent=_float(row.get("percent")),
        status=int(status) if status is not None else None,
        fantamedia=_float(row.get("fagrd")) or 0.0,
        index_compare=_float(row.get("indexCompare")) or 0.0,
        media_voto=_float(row.get("agrd")) or 0.0,
    )


def team_rates(fixtures: Iterable[tuple[str, str, int, int]]) -> dict[str, TeamRates]:
    """`(home, away, home_goals, away_goals)` fixtures into per-club relative rates.

    Relative to the league's goals per team-game, so 1.0 is an average side. An empty input
    gives an empty map, and every lookup against it is neutral.
    """
    scored: dict[str, float] = {}
    conceded: dict[str, float] = {}
    games: dict[str, int] = {}
    for home, away, hg, ag in fixtures:
        for club, gf, ga in ((home, hg, ag), (away, ag, hg)):
            scored[club] = scored.get(club, 0.0) + gf
            conceded[club] = conceded.get(club, 0.0) + ga
            games[club] = games.get(club, 0) + 1
    total_games = sum(games.values())
    if not total_games:
        return {}
    mean = sum(scored.values()) / total_games
    if mean <= 0:
        return {}
    return {
        club: TeamRates(
            attack=scored[club] / n / mean,
            defence_leak=conceded[club] / n / mean,
        )
        for club, n in games.items()
    }


def _clip(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def predict(
    rows: Sequence[RowSignals],
    *,
    cmday: int,
    prior_fantamedia: Mapping[int, float],
    prior_vote: Mapping[int, float] | None = None,
    past_clubs: Mapping[int, frozenset[str]],
    rates: Mapping[str, TeamRates],
    weights: PredictWeights | None = None,
) -> dict[int, Prediction]:
    """A `Prediction` per row. Every missing signal is a neutral factor, never an error — this
    runs unattended and a thin history must still produce a lineup."""
    w = weights or PredictWeights()
    games_this_season = max(cmday - 1, 0)

    prior_vote = prior_vote or {}
    draft: dict[int, tuple[float, float, dict[str, float]]] = {}
    votes: dict[int, float] = {}
    for r in rows:
        v_prior = prior_vote.get(r.pid) or DEFAULT_VOTE_PRIOR
        n_vote = games_this_season if r.media_voto > 0 else 0
        votes[r.pid] = (w.prior_games * v_prior + n_vote * r.media_voto) / (w.prior_games + n_vote)
        prior = prior_fantamedia.get(r.pid) or w.role_prior.get(r.role, 6.0)
        n = games_this_season if r.fantamedia > 0 else 0
        baseline = (w.prior_games * prior + n * r.fantamedia) / (w.prior_games + n)

        opp = 1.0
        rate = rates.get(r.opponent) if r.opponent else None
        if rate is not None:
            rel = rate.defence_leak if r.role in ("C", "A") else rate.attack
            sign = 1.0 if r.role in ("C", "A") else -1.0
            opp = _clip(1.0 + sign * w.opp_beta * (rel - 1.0), w.opp_clip)
        venue = 1.0 if r.home is None else (w.home if r.home else w.away)
        ex = w.ex_team if r.opponent and r.opponent in past_clubs.get(r.pid, frozenset()) else 1.0

        p_play = w.default_percent if r.percent is None else r.percent / 100.0
        if r.status is not None and r.status != 1:
            p_play *= w.status_doubt
        fv = baseline * opp * venue * ex
        draft[r.pid] = (
            p_play,
            fv,
            {"baseline": baseline, "opponent": opp, "venue": venue, "ex_team": ex},
        )

    # Rescale indexCompare onto the model's scale over this rosa, so the blend is not
    # dominated by whichever of the two runs larger. No usable indexCompare -> no blend.
    ic = {r.pid: r.index_compare for r in rows}
    ic_sum = sum(v for v in ic.values() if v > 0)
    model_sum = sum(p * fv for pid, (p, fv, _) in draft.items() if ic[pid] > 0)
    scale = model_sum / ic_sum if ic_sum > 0 else 0.0
    w_ic = w.ic_weight if scale > 0 else 0.0

    out: dict[int, Prediction] = {}
    for pid, (p_play, fv, factors) in draft.items():
        expected = p_play * fv
        out[pid] = Prediction(
            pid=pid,
            p_play=p_play,
            fv_if_plays=fv,
            expected=expected,
            score=(1.0 - w_ic) * expected + w_ic * scale * ic[pid],
            factors={**factors, "index_compare": ic[pid]},
            vote_if_plays=votes[pid],
        )
    return out
