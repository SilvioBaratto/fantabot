"""The empirical-Bayes projection: μ, the per-appearance sigma2, and the predictive sigma_tilde2.

Synthetic populations throughout, built so the right answer is known in closed form:
scores come in `base ± 1` pairs played on the same day, so the two weigh the same and each
player's weighted mean is exactly his base, whatever the half-life.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import _importgraph as G
import pytest

from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.projection import (
    Observation,
    ProjectionConfig,
    Target,
    observations,
    project,
    weights,
)
from fantabot.domain.lineup.scoring import Appearance, ScoringRules

AS_OF = date(2026, 1, 10)
CONFIG = ProjectionConfig(half_life_days=60.0)
TEAMS = ("INT", "NAP", "LEC", "ROM")
ROLE_BASE = {"DEF": 5.5, "MID": 6.0, "ATT": 6.5}
QI_SLOPE = 0.05
TEAM_EFFECT = {"INT": 0.6, "NAP": 0.2, "LEC": -0.5, "ROM": -0.3}


def _pairs(pid: int, base: float, *, count: int, qi: int, club: str) -> list[Observation]:
    """`count` appearances (even): a `base+1`/`base-1` pair each week back from AS_OF."""
    assert count % 2 == 0
    return [
        Observation(
            player_id=pid,
            played_on=AS_OF - timedelta(days=7 * (k // 2 + 1)),
            score=base + (1.0 if k % 2 == 0 else -1.0),
            qi=qi,
            club=club,
        )
        for k in range(count)
    ]


def _linear_world() -> tuple[list[Observation], dict[int, Target]]:
    """36 players whose true level is exactly role + slope·qi + team: no between-player
    noise at all, so the fitted prior must recover every level and τ² must be 0."""
    obs: list[Observation] = []
    players: dict[int, Target] = {}
    pid = 0
    for macro, role_base in ROLE_BASE.items():
        for club in TEAMS:
            for qi in (5, 15, 25):
                pid += 1
                base = role_base + QI_SLOPE * qi + TEAM_EFFECT[club]
                obs += _pairs(pid, base, count=8, qi=qi, club=club)
                players[pid] = Target(player_id=pid, macro=macro, qi=qi, club=club)
    return obs, players


class TestConfig:
    def test_the_half_life_has_no_default(self) -> None:
        """The backtest sweeps H; a silent default would grade a value nobody chose."""
        with pytest.raises(TypeError):
            ProjectionConfig()  # type: ignore[call-arg]

    @pytest.mark.parametrize("h", [0.0, -1.0, math.nan])
    def test_a_half_life_that_is_not_positive_is_refused(self, h: float) -> None:
        with pytest.raises(ValueError, match="half_life"):
            ProjectionConfig(half_life_days=h)

    def test_an_infinite_half_life_is_allowed(self) -> None:
        assert ProjectionConfig(half_life_days=math.inf).half_life_days == math.inf


class TestWeights:
    def test_a_weight_halves_every_half_life(self) -> None:
        w = weights([AS_OF - timedelta(days=d) for d in (0, 60, 120)], as_of=AS_OF,
                    half_life_days=60.0)
        assert list(w) == pytest.approx([1.0, 0.5, 0.25])

    def test_an_infinite_half_life_weighs_everything_1(self) -> None:
        w = weights([AS_OF - timedelta(days=d) for d in (1, 400)], as_of=AS_OF,
                    half_life_days=math.inf)
        assert list(w) == [1.0, 1.0]


def test_history_on_or_after_the_cutoff_is_refused() -> None:
    """The date filter lives in `history.before`; this is the backstop against a caller
    that forgot it, because a row from matchday g scoring matchday g is the worst leak."""
    obs, players = _linear_world()
    leaked = Observation(player_id=1, played_on=AS_OF, score=18.0, qi=5, club="INT")

    with pytest.raises(ValueError, match="before"):
        project([*obs, leaked], players, as_of=AS_OF, config=CONFIG)


class TestThePrior:
    def test_it_recovers_role_qi_and_team_exactly(self) -> None:
        obs, players = _linear_world()

        result = project(obs, players, as_of=AS_OF, config=CONFIG)

        for pid, target in players.items():
            qi, club = target.qi or 0, target.club or ""
            truth = ROLE_BASE[target.macro] + QI_SLOPE * qi + TEAM_EFFECT[club]
            assert result[pid].prior_mean == pytest.approx(truth, abs=1e-9), pid

    def test_no_current_qi_gets_the_role_mean_and_an_unknown_club_the_average(self) -> None:
        """stats-1: a player with no row for the season has no `qi` and no club."""
        obs, players = _linear_world()
        players[999] = Target(player_id=999, macro="MID", qi=None, club=None)

        result = project(obs, players, as_of=AS_OF, config=CONFIG)

        role_mean_qi = 15  # qi 5/15/25 equally weighted in every cell
        assert result[999].prior_mean == pytest.approx(
            ROLE_BASE["MID"] + QI_SLOPE * role_mean_qi, abs=1e-9
        )

    def test_a_club_never_seen_gets_no_team_effect(self) -> None:
        """Effect coding: team effects average to 0, so a promoted club is the average."""
        obs, players = _linear_world()
        players[998] = Target(player_id=998, macro="ATT", qi=25, club="PIS")

        result = project(obs, players, as_of=AS_OF, config=CONFIG)

        assert result[998].prior_mean == pytest.approx(
            ROLE_BASE["ATT"] + QI_SLOPE * 25 + sum(TEAM_EFFECT.values()) / len(TEAMS),
            abs=1e-9,
        )


class TestThePosterior:
    def test_no_history_gives_exactly_the_prior_and_the_analytic_spread(self) -> None:
        """n = 0: μ is the prior mean and sigma_tilde2 = s² + τ² — the role's appearance
        variance plus the uncertainty about the player's level."""
        obs, players = _linear_world()
        players[997] = Target(player_id=997, macro="DEF", qi=15, club="NAP")
        # Between-player spread the features cannot explain, so τ² is not 0. It must be
        # large: every third player is a qi-25 one, and the fit absorbs most of a small one.
        obs = [o if o.player_id % 3 else Observation(o.player_id, o.played_on, o.score + 3.0,
                                                      o.qi, o.club) for o in obs]

        result = project(obs, players, as_of=AS_OF, config=CONFIG)
        fresh = result[997]

        assert fresh.n == 0.0
        assert fresh.mu == fresh.prior_mean
        assert fresh.tau2 > 0
        assert fresh.sigma_tilde2 == fresh.s2 + fresh.tau2

    def test_a_long_history_converges_on_the_player_s_own_mean(self) -> None:
        obs, players = _linear_world()
        players[994] = Target(player_id=994, macro="MID", qi=15, club="ROM")
        obs += _pairs(994, 9.0, count=4000, qi=15, club="ROM")
        obs = [o if o.player_id % 3 else Observation(o.player_id, o.played_on, o.score + 0.8,
                                                      o.qi, o.club) for o in obs]

        result = project(obs, players, as_of=AS_OF, config=ProjectionConfig(math.inf))

        assert result[994].mu == pytest.approx(9.0, abs=0.01)
        assert result[994].v < 1e-3
        # ±1 pairs: his own appearance variance is exactly 1, and 4000 of them outweigh
        # the role's s² at 10 pseudo-appearances.
        assert result[994].sigma2 == pytest.approx(1.0, abs=0.02)

    def test_an_infinite_half_life_is_the_unweighted_model(self) -> None:
        """H = ∞ weighs every appearance exactly 1, so the effective count is the plain
        count; and it is the limit of a long finite H, not a special case bolted on."""
        obs, players = _linear_world()
        obs = [o if o.player_id % 3 else Observation(o.player_id, o.played_on, o.score + 0.8,
                                                      o.qi, o.club) for o in obs]

        unweighted = project(obs, players, as_of=AS_OF, config=ProjectionConfig(math.inf))
        very_long = project(obs, players, as_of=AS_OF, config=ProjectionConfig(1e12))

        for pid in players:
            assert unweighted[pid].n == 8.0
            assert unweighted[pid].mu == pytest.approx(very_long[pid].mu, abs=1e-9)
            assert unweighted[pid].sigma_tilde2 == pytest.approx(
                very_long[pid].sigma_tilde2, abs=1e-9
            )

    def test_recent_form_weighs_more_under_a_short_half_life(self) -> None:
        obs, players = _linear_world()
        players[995] = Target(player_id=995, macro="ATT", qi=25, club="INT")
        obs += [Observation(995, AS_OF - timedelta(days=300 + k), 12.0, 25, "INT")
                for k in range(6)]
        obs += [Observation(995, AS_OF - timedelta(days=5 + k), 5.0, 25, "INT") for k in range(6)]

        short = project(obs, players, as_of=AS_OF, config=ProjectionConfig(20.0))[995]
        flat = project(obs, players, as_of=AS_OF, config=ProjectionConfig(math.inf))[995]

        assert short.n < flat.n
        assert short.mu < flat.mu


def test_the_projection_reads_no_clock() -> None:
    """`as_of` is a parameter. A projection that read the clock would make the backtest
    and every test a function of the day they ran."""
    clock_reads = {"now", "today", "utcnow", "time", "monotonic", "perf_counter"}
    assert clock_reads & G.names_used("fantabot.domain.lineup.projection") == set()


class TestObservations:
    """From history rows to scored observations: the lega's score, the season's own `qi`,
    and a club only when the player's side resolves (SPEC A9)."""

    RULES = ScoringRules(
        goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
        penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=1.0,
        decisive_goal=1.0, threshold=66.0, steps=(6.0,),
    )

    def _row(self, season: str, home: str, away: str, **scored: int) -> HistoryAppearance:
        fixture = Fixture(season=season, giornata=1, played_on=date(2025, 9, 1), home=home,
                          away=away, home_goals=0, away_goals=0)
        return HistoryAppearance(player_id=7, role="A", fixture=fixture,
                                 scored=Appearance(voto_fc=6.0, **scored), fantavoto_fc=None)

    def test_the_score_is_the_lega_s_and_the_qi_the_season_s_own(self) -> None:
        valuations = {"2024/25": {7: Valuation(qi=11, squadra="LAZ")},
                      "2025/26": {7: Valuation(qi=19, squadra="LAZ")}}
        rows = [self._row("2024/25", "Lazio", "Genoa", gol_segnati=1),
                self._row("2025/26", "Udinese", "Lazio", mvp=1)]

        got = observations(rows, rules=self.RULES, valuations=valuations)

        # 2024/25 records `mvp`, so no expected rate is added; Lazio away still resolves.
        assert [(o.score, o.qi, o.club) for o in got] == [(9.0, 11, "LAZ"), (7.0, 19, "LAZ")]

    def test_an_unresolved_side_keeps_the_score_and_drops_the_club(self) -> None:
        """His club is neither end of the match: the row still tells us what he scored,
        and nothing about his team."""
        valuations = {"2025/26": {7: Valuation(qi=19, squadra="LAZ")}}

        (got,) = observations([self._row("2025/26", "Inter", "Genoa")], rules=self.RULES,
                              valuations=valuations)

        assert (got.score, got.qi, got.club) == (6.0, 19, None)

    def test_no_row_for_the_season_means_no_qi_and_no_club(self) -> None:
        (got,) = observations([self._row("2025/26", "Lazio", "Genoa")], rules=self.RULES,
                              valuations={})

        assert (got.qi, got.club) == (None, None)


class TestInformationAboutAPlayersMean:
    """SPEC A23 (amends A17). A player's weighted mean ȳ is known to within s²·Σw²/(Σw)²,
    not s²/Σw: with decaying weights the latter is too large. Measured on the lega's data
    at H = 180, subtracting it drove τ² to 0 and every μ to the prior. So the Kish count
    `n_eff = (Σw)²/Σw²` is what measures ȳ (the τ² moment, the posterior, v), and Σw stays
    the weight each observation carries."""

    def test_n_eff_is_the_kish_count_and_the_posterior_uses_it(self) -> None:
        obs, players = _linear_world()
        obs = [o if o.player_id % 3 else Observation(o.player_id, o.played_on, o.score + 3.0,
                                                      o.qi, o.club) for o in obs]
        players[993] = Target(player_id=993, macro="MID", qi=15, club="NAP")
        # Two appearances one half-life apart: weights w and w/2, whatever w is.
        obs += [Observation(993, AS_OF - timedelta(days=1), 10.0, 15, "NAP"),
                Observation(993, AS_OF - timedelta(days=61), 4.0, 15, "NAP")]

        got = project(obs, players, as_of=AS_OF, config=CONFIG)[993]

        assert got.n_eff == pytest.approx(1.8)  # (1.5w)² / 1.25w²
        ybar = (10.0 * 2 + 4.0) / 3  # weights 2:1
        shrink = got.n_eff * got.tau2 / (got.n_eff * got.tau2 + got.s2)
        assert got.mu == pytest.approx(got.prior_mean + shrink * (ybar - got.prior_mean))
        assert got.v == pytest.approx(got.tau2 * (1 - shrink))

    def test_a_small_true_spread_survives_heavy_decay(self) -> None:
        """300 players, a year of weekly appearances, H = 30 days, true τ² = 0.09 against
        an appearance spread of 4. The weighted means are noisy, and the moment must
        subtract exactly that noise: s²/Σw would subtract twice as much and floor at 0."""
        import numpy as np

        rng = np.random.default_rng(20260922)
        obs = []
        players = {}
        for pid in range(1, 301):
            level = 6.0 + rng.normal(0.0, 0.3)
            players[pid] = Target(player_id=pid, macro="MID", qi=10, club="INT")
            obs += [
                Observation(pid, AS_OF - timedelta(days=7 * k), float(level + rng.normal(0, 2)),
                            10, "INT")
                for k in range(1, 53)
            ]

        tau2 = next(iter(project(obs, players, as_of=AS_OF,
                                 config=ProjectionConfig(30.0)).values())).tau2

        assert 0.03 < tau2 < 0.2, tau2


class TestTheWeightsReachEveryEstimate:
    """In the worlds above every player's own spread equals his role's, and the prior is
    noise-free: there, a fit that ignored the weights would give the same answer. These
    worlds are built so it cannot."""

    def test_sigma2_shrinks_the_role_s_variance_toward_his_own(self) -> None:
        obs, players = _linear_world()
        players[992] = Target(player_id=992, macro="MID", qi=15, club="INT")
        wild = _pairs(992, 6.8, count=20, qi=15, club="INT")
        obs += [Observation(o.player_id, o.played_on, 6.8 + 4.0 * (o.score - 6.8), o.qi,
                            o.club) for o in wild]  # ±4: own SS = 20·16 = 320

        got = project(obs, players, as_of=AS_OF, config=ProjectionConfig(math.inf))[992]

        assert got.sigma2 == pytest.approx(got.s2 + (320.0 - 20 * got.s2) / (10 + 20))
        assert got.sigma2 > got.s2

    def test_the_prior_is_a_weighted_fit(self) -> None:
        """Two players with identical features: an old 9 and a recent 5. The prior for a
        third like them is their *weighted* mean, not their plain one."""
        old = _pairs(1, 9.0, count=8, qi=10, club="INT")
        old = [Observation(1, o.played_on - timedelta(days=300), o.score, 10, "INT") for o in old]
        recent = _pairs(2, 5.0, count=8, qi=10, club="INT")
        players = {pid: Target(pid, "DEF", 10, "INT") for pid in (1, 2, 3)}

        got = project([*old, *recent], players, as_of=AS_OF, config=CONFIG)[3]

        w_old = weights([o.played_on for o in old], as_of=AS_OF, half_life_days=60.0).sum()
        w_new = weights([o.played_on for o in recent], as_of=AS_OF, half_life_days=60.0).sum()
        assert got.prior_mean == pytest.approx((9.0 * w_old + 5.0 * w_new) / (w_old + w_new))

    def test_a_missing_qi_takes_the_role_s_weighted_mean_qi(self) -> None:
        old = [Observation(1, o.played_on - timedelta(days=300), 5.5 + 0.1 * 5 + (o.score - 6),
                           5, "INT") for o in _pairs(1, 6.0, count=8, qi=5, club="INT")]
        recent = [Observation(2, o.played_on, 5.5 + 0.1 * 25 + (o.score - 6), 25, "INT")
                  for o in _pairs(2, 6.0, count=8, qi=25, club="INT")]
        players = {1: Target(1, "DEF", 5, "INT"), 2: Target(2, "DEF", 25, "INT"),
                   3: Target(3, "DEF", None, "INT")}

        got = project([*old, *recent], players, as_of=AS_OF, config=CONFIG)[3]

        w_old = weights([o.played_on for o in old], as_of=AS_OF, half_life_days=60.0).sum()
        w_new = weights([o.played_on for o in recent], as_of=AS_OF, half_life_days=60.0).sum()
        mean_qi = (5 * w_old + 25 * w_new) / (w_old + w_new)
        assert got.prior_mean == pytest.approx(5.5 + 0.1 * mean_qi)
