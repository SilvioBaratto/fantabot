"""Presence: P(a player gets a vote), from his last team matches and the news. Pure.

The beta-binomial populations are built so the moment estimator has a closed form: every
player has the same number of team matches n, where the estimator reduces to the ANOVA one,
`rho = (n·V/(μ(1-μ)) - 1)/(n - 1)` with V the sample variance of the rates, and the prior's
concentration is `1/rho - 1`.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import _importgraph as G
import pytest

from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.presence import (
    BetaPrior,
    PresenceWeights,
    Window,
    cal,
    fit_priors,
    p_hist,
    presence,
    windows,
)
from fantabot.domain.lineup.scoring import Appearance
from fantabot.domain.shared.values import SentimentRow

CUTOFF = date(2026, 9, 20)
NOW = "2026/27"
LAST = "2025/26"


def _fixture(season: str, giornata: int, played_on: date, home: str, away: str) -> Fixture:
    return Fixture(
        season=season, giornata=giornata, played_on=played_on,
        home=home, away=away, home_goals=0, away_goals=0,
    )


def _voted(player_id: int, fixture: Fixture) -> HistoryAppearance:
    return HistoryAppearance(
        player_id=player_id, role="C", fixture=fixture,
        scored=Appearance(voto_fc=6.0), fantavoto_fc=6.0,
    )


def _reading(
    player_id: str = "7",
    *,
    run: str = "2026-09-20",
    disponibilita: float = 1.0,
    titolarita: float = 1.0,
    confidenza: float = 1.0,
) -> SentimentRow:
    return SentimentRow(
        player_id=player_id, nome=f"p{player_id}", data_run=run, sentiment=0.0,
        disponibilita=disponibilita, titolarita=titolarita, mercato=0.0, forma=0.0,
        rigorista=0.0, piazzati=0.0, confidenza=confidenza, ruolo_campo="",
        ruoli_mantra="", deriva_ruolo=0.0,
    )


#: Inter's first three 2026/27 matches before the cutoff, and a fourth on the cutoff day.
INT_NOW = [
    _fixture(NOW, 1, date(2026, 8, 23), "Inter", "Lecce"),
    _fixture(NOW, 2, date(2026, 8, 30), "Roma", "Inter"),
    _fixture(NOW, 3, date(2026, 9, 13), "Inter", "Genoa"),
]
INT_ON_CUTOFF = _fixture(NOW, 4, CUTOFF, "Lazio", "Inter")
#: Napoli's last five 2025/26 matches, g34..g38.
NAP_LAST = [
    _fixture(LAST, 34 + i, date(2026, 4, 26) + timedelta(days=7 * i), "Napoli", f"Club{i}")
    for i in range(5)
]
#: A match Inter did not play.
ELSEWHERE = _fixture(NOW, 2, date(2026, 8, 30), "Lazio", "Genoa")
FIXTURES = [*INT_NOW, INT_ON_CUTOFF, *NAP_LAST, ELSEWHERE]
#: Player 1 is Inter's this season and was Napoli's last season.
VALUATIONS = {
    NOW: {1: Valuation(qi=20, squadra="INT")},
    LAST: {1: Valuation(qi=18, squadra="NAP")},
}


class TestWindows:
    def test_the_last_k_team_matches_cross_into_last_season_through_that_season_s_club(
        self,
    ) -> None:
        """K = 6 at giornata 4: Inter's three, then Napoli's last three (g36-g38). The g34
        vote is outside the window."""
        votes = [INT_NOW[0], INT_NOW[2], NAP_LAST[4], NAP_LAST[2], NAP_LAST[0]]
        out = windows(
            [_voted(1, f) for f in votes], FIXTURES, VALUATIONS, [1], cutoff=CUTOFF, k=6
        )
        assert out[1] == Window(votes=4, matches=6)

    def test_a_short_window_is_the_most_recent_matches(self) -> None:
        votes = [INT_NOW[2], NAP_LAST[4]]
        out = windows(
            [_voted(1, f) for f in votes], FIXTURES, VALUATIONS, [1], cutoff=CUTOFF, k=2
        )
        assert out[1] == Window(votes=1, matches=2)

    def test_a_match_on_the_cutoff_day_is_neither_a_match_nor_a_vote(self) -> None:
        out = windows(
            [_voted(1, INT_ON_CUTOFF)], FIXTURES, VALUATIONS, [1], cutoff=CUTOFF, k=3
        )
        assert out[1] == Window(votes=0, matches=3)

    def test_a_row_in_a_match_his_club_did_not_play_is_not_a_vote(self) -> None:
        """A9: an unresolvable row is dropped from p_hist, not counted against a team match."""
        out = windows(
            [_voted(1, ELSEWHERE)], FIXTURES, VALUATIONS, [1], cutoff=CUTOFF, k=3
        )
        assert out[1] == Window(votes=0, matches=3)

    def test_no_club_in_any_season_is_an_empty_window(self) -> None:
        out = windows([], FIXTURES, VALUATIONS, [2], cutoff=CUTOFF, k=6)
        assert out[2] == Window(votes=0, matches=0)

    def test_his_new_club_s_matches_from_last_season_are_not_his(self) -> None:
        """Inter played in 2025/26 too, but he was Napoli's then: the window is Inter's
        three, then Napoli's g36-g38, and the g36 vote is in it."""
        int_last = _fixture(LAST, 38, NAP_LAST[4].played_on, "Inter", "Torino")
        out = windows(
            [_voted(1, NAP_LAST[2])], [*FIXTURES, int_last], VALUATIONS, [1],
            cutoff=CUTOFF, k=6,
        )
        assert out[1] == Window(votes=1, matches=6)

    def test_another_player_s_vote_is_not_his(self) -> None:
        valuations = {NOW: {1: Valuation(20, "INT"), 2: Valuation(20, "INT")}}
        out = windows(
            [_voted(2, INT_NOW[2])], FIXTURES, valuations, [1, 2], cutoff=CUTOFF, k=3
        )
        assert out == {1: Window(0, 3), 2: Window(1, 3)}


def _population(*records: tuple[int, int], role: str = "C", start: int = 1) -> tuple[
    dict[int, Window], dict[int, str]
]:
    wins = {start + i: Window(votes=v, matches=n) for i, (v, n) in enumerate(records)}
    return wins, {pid: role for pid in wins}


class TestPriors:
    def test_the_moment_estimator_has_the_closed_form(self) -> None:
        """Rates 1, 1, ½, ½ over n = 6: μ = ¾, V = 1/12, rho = (6·(1/12)/(3/16) - 1)/5 = 1/3,
        so the concentration is 1/rho - 1 = 2."""
        wins, roles = _population((6, 6), (6, 6), (3, 6), (3, 6))
        prior = fit_priors(wins, roles)["C"]
        assert prior.mean == pytest.approx(0.75)
        assert prior.concentration == pytest.approx(2.0)

    def test_p_hist_is_the_posterior_mean(self) -> None:
        prior = BetaPrior(mean=0.75, concentration=2.0)
        assert p_hist(Window(3, 6), prior) == pytest.approx((1.5 + 3) / (2 + 6))
        assert p_hist(Window(6, 6), prior) == pytest.approx((1.5 + 6) / (2 + 6))

    def test_no_team_matches_is_exactly_the_prior_mean(self) -> None:
        assert p_hist(Window(0, 0), BetaPrior(mean=0.75, concentration=2.0)) == 0.75
        assert p_hist(Window(0, 0), BetaPrior(mean=0.75, concentration=0.0)) == 0.75

    def test_no_spread_between_players_means_everyone_is_the_mean(self) -> None:
        wins, roles = _population((3, 6), (3, 6), (3, 6), (3, 6))
        prior = fit_priors(wins, roles)["C"]
        assert prior.concentration == math.inf
        assert p_hist(Window(6, 6), prior) == 0.5

    def test_spread_beyond_binomial_noise_means_each_player_is_his_own_rate(self) -> None:
        """Rates 1, 1, 0, 0 over n = 6 give rho = 1.4 ≥ 1: the prior carries no weight."""
        wins, roles = _population((6, 6), (6, 6), (0, 6), (0, 6))
        prior = fit_priors(wins, roles)["C"]
        assert prior.concentration == 0.0
        assert p_hist(Window(6, 6), prior) == 1.0
        assert p_hist(Window(0, 6), prior) == 0.0

    def test_each_role_gets_its_own_prior(self) -> None:
        mids, mid_roles = _population((6, 6), (6, 6), (3, 6), (3, 6), role="C")
        keepers, keeper_roles = _population((6, 6), (5, 6), (6, 6), (1, 6), role="P", start=10)
        priors = fit_priors({**mids, **keepers}, {**mid_roles, **keeper_roles})
        assert priors["C"].mean == pytest.approx(18 / 24)
        assert priors["P"].mean == pytest.approx(18 / 24)
        assert priors["P"].concentration != pytest.approx(priors["C"].concentration)
        assert priors["*"].mean == pytest.approx(36 / 48)

    def test_a_role_the_moments_cannot_fit_takes_the_pooled_prior(self) -> None:
        """One forward is no population, and keepers who all played every match show no
        spread to measure: both borrow the pool of every role."""
        mids, mid_roles = _population((6, 6), (6, 6), (3, 6), (3, 6), role="C")
        keepers, keeper_roles = _population((6, 6), (6, 6), role="P", start=10)
        forward, forward_role = _population((2, 6), role="A", start=20)
        priors = fit_priors(
            {**mids, **keepers, **forward}, {**mid_roles, **keeper_roles, **forward_role}
        )
        assert priors["P"] == priors["*"]
        assert priors["A"] == priors["*"]
        assert priors["*"].mean == pytest.approx(32 / 42)

    def test_players_without_team_matches_do_not_move_the_fit(self) -> None:
        wins, roles = _population((6, 6), (6, 6), (3, 6), (3, 6), (0, 0))
        prior = fit_priors(wins, roles)["C"]
        assert (prior.mean, prior.concentration) == pytest.approx((0.75, 2.0))

    def test_a_population_with_nothing_to_fit_is_refused(self) -> None:
        """One match each cannot tell a player's spread from binomial noise."""
        wins, roles = _population((1, 1), (0, 1), (1, 1))
        with pytest.raises(ValueError, match="too thin"):
            fit_priors(wins, roles)


class TestCal:
    """`cal` puts titolarita on the vote scale (A17(4), as amended by A24)."""

    GRID = (0.0, 0.1, 0.35, 0.5, 0.8, 1.0)

    @pytest.mark.parametrize("d", GRID)
    @pytest.mark.parametrize("q", [0.15, 0.35, 0.55, 0.6, 1.0])
    def test_it_is_monotone_in_t_and_cal_1_is_1(self, d: float, q: float) -> None:
        values = [cal(t, d=d, q=q) for t in self.GRID]
        assert values == sorted(values)
        assert cal(1.0, d=d, q=q) == 1.0

    def test_at_full_availability_it_is_a17(self) -> None:
        for t in self.GRID:
            assert cal(t, d=1.0, q=0.55) == pytest.approx(t + (1 - t) * 0.55)

    def test_an_unavailable_player_does_not_come_off_the_bench(self) -> None:
        """A24: A17(4) as written gave d = 0, t = 0 a vote probability of q_role."""
        assert cal(0.0, d=0.0, q=0.55) == 0.0

    def test_only_the_available_share_that_does_not_start_comes_off_the_bench(self) -> None:
        assert cal(0.4, d=0.5, q=0.55) == pytest.approx(0.4 + 0.1 * 0.55)

    def test_a_start_reading_above_availability_is_taken_as_it_is(self) -> None:
        assert cal(0.6, d=0.3, q=0.55) == 0.6


class TestWeights:
    def test_the_declared_priors(self) -> None:
        """A17(4)'s q_role; K = 6 (the operator, 2026-09-22); the asta's 7-day half-life."""
        weights = PresenceWeights()
        assert dict(weights.q_role) == {"A": 0.6, "C": 0.55, "D": 0.35, "P": 0.15}
        assert weights.k == 6
        assert weights.half_life_days == 7.0

    @pytest.mark.parametrize("k", [0, -1])
    def test_an_empty_window_is_refused(self, k: int) -> None:
        with pytest.raises(ValueError, match="k"):
            PresenceWeights(k=k)

    @pytest.mark.parametrize("q", [-0.1, 1.1, math.nan])
    def test_a_bench_rate_outside_0_1_is_refused(self, q: float) -> None:
        with pytest.raises(ValueError, match="q_role"):
            PresenceWeights(q_role={"A": q, "C": 0.55, "D": 0.35, "P": 0.15})

    def test_a_missing_role_is_refused(self) -> None:
        with pytest.raises(ValueError, match="q_role"):
            PresenceWeights(q_role={"A": 0.6, "C": 0.55, "D": 0.35})

    @pytest.mark.parametrize("h", [0.0, -7.0, math.nan])
    def test_a_half_life_that_is_not_positive_is_refused(self, h: float) -> None:
        with pytest.raises(ValueError, match="half_life"):
            PresenceWeights(half_life_days=h)


#: A midfielder population whose prior is mean ¾, concentration 2; player 7 is 3 of 6.
WINS, ROLES = _population((6, 6), (6, 6), (3, 6), (3, 6), start=5)
AS_OF = date(2026, 9, 20)
P_HIST_7 = (1.5 + 3) / (2 + 6)


def _p(sentiment: dict[int, SentimentRow], pid: int = 7) -> float:
    return presence(WINS, ROLES, sentiment, as_of=AS_OF)[pid].p


class TestPresence:
    def test_with_no_news_p_is_p_hist(self) -> None:
        out = presence(WINS, ROLES, {}, as_of=AS_OF)[7]
        assert out.p == out.p_hist == pytest.approx(P_HIST_7)
        assert out.news_weight == 0.0

    def test_a_silent_reading_is_no_news(self) -> None:
        silent = _reading(disponibilita=0.0, titolarita=0.0, confidenza=0.0)
        assert _p({7: silent}) == pytest.approx(P_HIST_7)

    def test_a_fresh_certain_reading_is_the_vote_scale_alone(self) -> None:
        reading = _reading(disponibilita=1.0, titolarita=0.3, confidenza=1.0)
        assert _p({7: reading}) == pytest.approx(cal(0.3, d=1.0, q=0.55))

    def test_a_fresh_certain_injury_has_no_vote(self) -> None:
        """A24: to the letter of A17(4) this was q_role = 0.55."""
        injured = _reading(disponibilita=0.0, titolarita=0.0, confidenza=1.0)
        assert _p({7: injured}) == 0.0

    def test_a_week_old_injury_counts_half(self) -> None:
        """w = ½ one half-life on, so d_eff = ½ and p = (1 - ½)·½·p_hist + ½·0."""
        injured = _reading(run="2026-09-13", disponibilita=0.0, titolarita=0.0)
        out = presence(WINS, ROLES, {7: injured}, as_of=AS_OF)[7]
        assert out.news_weight == pytest.approx(0.5)
        assert out.p == pytest.approx(0.25 * P_HIST_7)

    def test_a_stale_injury_decays_back_to_p_hist(self) -> None:
        """Ten half-lives on, disponibilita 0.00 has all but stopped mattering."""
        injured = _reading(run="2026-07-12", disponibilita=0.0, titolarita=0.0)
        assert _p({7: injured}) == pytest.approx(P_HIST_7, abs=2e-3)
        assert _p({7: injured}) < P_HIST_7

    def test_the_half_life_is_the_weights_own(self) -> None:
        injured = _reading(run="2026-09-06", disponibilita=0.0, titolarita=0.0)
        out = presence(
            WINS, ROLES, {7: injured}, as_of=AS_OF, weights=PresenceWeights(half_life_days=14)
        )[7]
        assert out.news_weight == pytest.approx(0.5)

    def test_each_role_uses_its_own_prior(self) -> None:
        mids, mid_roles = _population((6, 6), (6, 6), (3, 6), (3, 6), role="C")
        keepers, keeper_roles = _population((6, 6), (5, 6), (6, 6), (1, 6), role="P", start=10)
        wins, roles = {**mids, **keepers}, {**mid_roles, **keeper_roles}
        priors = fit_priors(wins, roles)
        out = presence(wins, roles, {}, as_of=AS_OF)
        assert out[13].p == pytest.approx(p_hist(Window(1, 6), priors["P"]))
        assert out[13].p != pytest.approx(p_hist(Window(1, 6), priors["*"]))

    def test_each_role_uses_its_own_bench_rate(self) -> None:
        wins, roles = _population((6, 6), (6, 6), (3, 6), (3, 6), role="P", start=5)
        reading = _reading(titolarita=0.0, confidenza=1.0)
        assert presence(wins, roles, {7: reading}, as_of=AS_OF)[7].p == pytest.approx(0.15)

    @pytest.mark.parametrize("d", [0.0, 0.5, 1.0])
    @pytest.mark.parametrize("t", [0.0, 0.5, 1.0])
    @pytest.mark.parametrize("c", [0.0, 0.4, 1.0])
    @pytest.mark.parametrize("run", ["2026-09-20", "2026-09-17", "2026-06-01"])
    @pytest.mark.parametrize("record", [(0, 6), (6, 6), (0, 0)])
    def test_p_stays_within_0_1(
        self, d: float, t: float, c: float, run: str, record: tuple[int, int]
    ) -> None:
        wins = {**WINS, 7: Window(*record)}
        reading = _reading(run=run, disponibilita=d, titolarita=t, confidenza=c)
        p = presence(wins, ROLES, {7: reading}, as_of=AS_OF)[7].p
        assert 0.0 <= p <= 1.0

    def test_the_news_joins_on_str_of_the_id(self) -> None:
        """Sentiment ids are strings; the port keys them by int. A reading reaches the
        player whose `str(id)` it carries."""
        rows = [_reading("7", disponibilita=0.0, titolarita=0.0)]
        assert _p({int(row.player_id): row for row in rows}) == 0.0

    def test_a_reading_filed_under_another_id_is_refused(self) -> None:
        """Keyed wrong, one player's injury would silently become another's."""
        with pytest.raises(ValueError, match="8"):
            _p({7: _reading("8")})

    def test_a_player_without_a_window_gets_his_role_s_prior(self) -> None:
        roles = {**ROLES, 99: "C"}
        assert presence(WINS, roles, {}, as_of=AS_OF)[99].p == pytest.approx(0.75)

    def test_an_unknown_role_is_refused(self) -> None:
        with pytest.raises(KeyError):
            presence(WINS, {**ROLES, 99: "M"}, {}, as_of=AS_OF)


def test_presence_reads_no_clock() -> None:
    clock_reads = {"now", "today", "utcnow", "time", "monotonic", "perf_counter"}
    assert clock_reads & G.names_used("fantabot.domain.lineup.presence") == set()
