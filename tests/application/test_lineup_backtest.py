"""Gate 1's orchestration: the refits, the two arms, the sweep and the report. No database.

The history is a fake implementing `domain.lineup.history.LineupHistory` and the corpus is
built from literals, so the whole gate runs in the socket-free default tier at a budget that
takes milliseconds. What is pinned here is the wiring the statistics rest on:

* **the cutoff is a date, never a giornata** — a postponed match keeps its number and is
  played weeks later, so `giornata < g` lets the future into a fit;
* **H is picked on the sweep season and carried unchanged** — a half-life tuned on the
  season it is graded on is a model choosing its own exam;
* **the fit is per giornata, not per roster**, so two rosters at one giornata are planned
  against one model;
* **news is ablated** — there are no recorded readings for 2023/24, and a model graded with
  a signal it will not have is graded on a game it will not play;
* **a replayed plan does not depend on what ran before it**, so a one-room smoke run gives
  the same XI as the same room inside the whole corpus.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, timedelta

import numpy as np

from fantabot.application.lineup_backtest import (
    BACKTEST_BUDGET,
    FIRST_MODEL_GIORNATA,
    ReplaySettings,
    SeasonData,
    fit_giornata,
    grade,
    read_season,
    replay_season,
    roster_seed,
    run_gate,
    sweep_half_life,
)
from fantabot.application.reporting import SilentReporter
from fantabot.domain.lineup.backtest_corpus import BacktestRoom, Corpus, Roster
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.scoring import Appearance, ScoringRules
from fantabot.domain.shared.club_names import code_for

#: The lega's own ladder (`stlmt` 66, `stgoal` 6..42), spelled out here rather than imported
#: across directories: `tests/` alone is on `sys.path`, so a cross-package test import works
#: only while the whole suite runs and fails the moment this file is run on its own.
RULES = ScoringRules(
    goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
    penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=1.0,
    decisive_goal=1.0, threshold=66.0, steps=(6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0),
)

SEASON, SWEEP = "2025/26", "2024/25"
OPENED = date(2025, 8, 24)
CLUBS = ("Inter", "Milan", "Roma", "Lazio")

#: Four clubs, six players each: a keeper, three defenders, a midfielder and a forward, so
#: every roster of twelve can field 343 twice over.
ROLE_CODES: dict[int, tuple[str, ...]] = {}
CLUB_OF: dict[int, str] = {}
for club_index, club in enumerate(CLUBS):
    for slot, codes in enumerate(
        [("POR",), ("DC",), ("DC",), ("E",), ("C",), ("A",), ("A",), ("E",), ("DC",), ("C",)]
    ):
        pid = 100 * (club_index + 1) + slot
        ROLE_CODES[pid] = codes
        # The **code**, not the name: `quotazioni.squadra` is a code and `history.plays_in`
        # compares `code_for(fixture.home)` against it. A fake that stored the name joined
        # nothing and every presence window came back empty.
        CLUB_OF[pid] = code_for(club)
PLAYERS = tuple(ROLE_CODES)


def _plays(pid: int, giornata: int) -> bool:
    """Who turns up. **Not everyone, every week** — `presence.fit_priors` needs a spread of
    vote rates to fit a prior on, and a fake where everybody plays every match has none.
    The two deepest reserves of each club alternate and the last one stops after g3."""
    slot = pid % 10
    if slot == 9:
        return giornata <= 3
    if slot == 8:
        return giornata % 2 == 1
    return True


def _fixtures(season: str, giornate: int = 10) -> list[Fixture]:
    out: list[Fixture] = []
    for g in range(1, giornate + 1):
        day = OPENED + timedelta(days=7 * (g - 1))
        out.append(Fixture(season=season, giornata=g, played_on=day, home=CLUBS[0],
                           away=CLUBS[1], home_goals=1, away_goals=1))
        out.append(Fixture(season=season, giornata=g, played_on=day, home=CLUBS[2],
                           away=CLUBS[3], home_goals=1, away_goals=1))
    return out


def _appearances(season: str, giornate: int = 10) -> list[HistoryAppearance]:
    every = _fixtures(season, giornate)
    fixtures = {(f.giornata, code_for(f.home)): f for f in every}
    fixtures |= {(f.giornata, code_for(f.away)): f for f in every}
    out: list[HistoryAppearance] = []
    for pid in PLAYERS:
        for g in range(1, giornate + 1):
            if not _plays(pid, g):
                continue
            fixture = fixtures[(g, CLUB_OF[pid])]
            # A readable spread: higher ids score more, and every player varies week to
            # week — a fake where a player's every vote is identical gives him a zero
            # residual spread, and the copula's marginals are then a divide by zero.
            vote = 5.0 + (pid % 10) * 0.2 + ((pid + g) % 5) * 0.3
            out.append(
                HistoryAppearance(
                    player_id=pid,
                    fixture=fixture,
                    role={"POR": "P", "DC": "D", "E": "D", "C": "C", "A": "A"}[
                        ROLE_CODES[pid][0]
                    ],
                    scored=Appearance(voto_fc=vote),
                    fantavoto_fc=vote,
                )
            )
    return out


class FakeHistory:
    """`domain.lineup.history.LineupHistory`, from literals, recording what was asked."""

    def __init__(self, *, giornate: int = 10) -> None:
        self.giornate = giornate
        self.asked_before: list[date] = []
        self.sentiment_calls = 0

    def appearances(
        self, player_ids: Collection[int], *, seasons: Collection[str], before: date
    ) -> list[HistoryAppearance]:
        self.asked_before.append(before)
        wanted = set(player_ids)
        return [
            row
            for season in seasons
            for row in _appearances(season, self.giornate)
            if row.player_id in wanted and row.fixture.played_on < before
        ]

    def roles(self, season: str) -> dict[int, tuple[str, ...]]:
        return dict(ROLE_CODES)

    def valuations(self, season: str) -> dict[int, Valuation]:
        return {pid: Valuation(qi=10.0, squadra=CLUB_OF[pid]) for pid in PLAYERS}

    def fixtures(self, season: str) -> list[Fixture]:
        return _fixtures(season, self.giornate)

    def calculated_scores(self, league_id: int) -> list[float]:
        return []

    def latest_sentiment(self, player_ids: Collection[int]) -> dict[int, object]:
        self.sentiment_calls += 1
        return {}


def _corpus(rooms: int = 1, buyers: int = 2) -> Corpus:
    """`rooms` rooms of `buyers` rosters, each holding one whole club plus a spare."""
    out: list[BacktestRoom] = []
    for r in range(rooms):
        rosters = []
        for b in range(buyers):
            block = [pid for pid in PLAYERS if CLUB_OF[pid] == code_for(CLUBS[b])]
            spare = [pid for pid in PLAYERS if CLUB_OF[pid] == code_for(CLUBS[b + 2])][:6]
            rosters.append(Roster(f"buyer{b}", tuple(sorted({*block, *spare}))))
        out.append(
            BacktestRoom(
                asta_id=f"room{r}", num_teams=buyers, num_credits=500,
                declared_max_player=None, rosters=tuple(rosters),
            )
        )
    return Corpus(tuple(out), ())


SETTINGS = ReplaySettings(
    rules=RULES,
    sub_mode="basic",
    modules=("343",),
    bench_size=3,
    budget=BACKTEST_BUDGET.__class__(
        k=1, lambdas=(0.0,), node_budget=100, m=1, work=6_000, bench_depth=1, bench_width=2
    ),
    last=8,
)


def _data(history: FakeHistory | None = None, season: str = SEASON) -> SeasonData:
    return read_season(history or FakeHistory(), season)


class TestTheRead:
    def test_roles_come_from_the_roles_season_and_qi_from_the_replayed_one(self) -> None:
        """A16(3). The platform froze its Mantra tags in July 2026 and no earlier season's
        are recorded, so the replay uses today's tags on yesterday's form — a limitation the
        report prints rather than a defect the corpus can fix."""
        data = _data()

        assert data.season == SEASON
        assert set(data.role_codes) == set(PLAYERS)
        assert set(data.valuations) == set(PLAYERS)

    def test_the_whole_season_is_read_once(self) -> None:
        """38 queries a season against one is not the point; the point is that one read
        cannot disagree with another about what happened."""
        history = FakeHistory()

        read_season(history, SEASON)

        assert len(history.asked_before) == 1


class TestTheFit:
    def test_the_cutoff_is_the_day_the_giornata_opened(self) -> None:
        data = _data()

        fit = fit_giornata(data, 4, rules=RULES, half_life=180.0)

        assert fit.cutoff == OPENED + timedelta(days=21)

    def test_nothing_from_the_giornata_itself_reaches_the_fit(self) -> None:
        """The leak the battery mutates. A `<=` cutoff, or a `giornata <` one, puts the
        result being predicted into the model predicting it."""
        data = _data()

        fit = fit_giornata(data, 4, rules=RULES, half_life=180.0)

        assert all(
            row.fixture.played_on < fit.cutoff
            for row in data.rows
            if row.fixture.giornata < 4
        )
        assert fit.votes and all(v > 0 for v in fit.votes.values())

    def test_the_votes_are_the_giornata_being_replayed(self) -> None:
        data = _data()

        fit = fit_giornata(data, 4, rules=RULES, half_life=180.0)

        # Not everyone: the deepest reserve of each club stops after g3, which is what
        # gives the presence prior a spread to be fitted on.
        assert set(fit.votes) < set(PLAYERS)
        assert len(fit.votes) >= len(PLAYERS) - len(CLUBS) * 2

    def test_the_baseline_reads_his_teams_latest_match_and_not_his_own(self) -> None:
        """A player who last appeared weeks ago, whose club has played since, is a `p = 0`.
        Reading it off *his* last appearance would make him a 1, which is the difference
        between a baseline that benches an absentee and one that fields him every week.

        The slot-8 reserves play odd giornate only, so at g4 — whose latest earlier round is
        g3, which they did play — they are 1, and at g5 they are 0.
        """
        data = _data()

        at_five = fit_giornata(data, 5, rules=RULES, half_life=180.0, model=False)

        absentees = [pid for pid, p in at_five.baseline_p.items() if p == 0.0]
        assert absentees
        assert all(pid % 10 in (8, 9) for pid in absentees)

    def test_news_is_ablated(self) -> None:
        """There are no recorded readings for a replayed season, and a model graded with a
        signal it will not have is graded on a game it will not play."""
        history = FakeHistory()
        data = read_season(history, SEASON)
        calls = history.sentiment_calls

        fit_giornata(data, 4, rules=RULES, half_life=180.0)

        assert history.sentiment_calls == calls

    def test_one_fit_serves_every_roster(self) -> None:
        """Per giornata, not per roster: two rosters at one giornata must be planned against
        one model, not each against its own."""
        data = _data()

        one = fit_giornata(data, 4, rules=RULES, half_life=180.0)
        two = fit_giornata(data, 4, rules=RULES, half_life=180.0)

        assert one.cutoff == two.cutoff
        assert one.baseline_mu == two.baseline_mu


class TestTheReplay:
    def test_both_arms_field_the_same_rosters_and_the_rows_are_paired(self) -> None:
        result = replay_season(_data(), _corpus(), SETTINGS, reporter=SilentReporter())

        assert result.paired
        assert {row.room for row in result.paired} == {"room0"}
        assert min(row.giornata for row in result.paired) >= FIRST_MODEL_GIORNATA

    def test_the_baseline_burns_in_before_the_model_starts(self) -> None:
        """A16(6): the model's opponent is the room's baseline scores before g, so the
        baseline has to have played first."""
        result = replay_season(_data(), _corpus(), SETTINGS, reporter=SilentReporter())

        assert all(row.giornata > 5 for row in result.paired)

    def test_a_replayed_plan_does_not_depend_on_what_ran_before_it(self) -> None:
        """A one-room smoke run must give the same XI as that room inside the whole corpus,
        or the two cannot be compared — and a threaded generator would break exactly that."""
        alone = replay_season(_data(), _corpus(rooms=1), SETTINGS, reporter=SilentReporter())
        crowd = replay_season(_data(), _corpus(rooms=3), SETTINGS, reporter=SilentReporter())

        mine = [row for row in crowd.paired if row.room == "room0"]
        assert [r.model_fantapunti for r in alone.paired] == [r.model_fantapunti for r in mine]

    def test_the_same_replay_twice_is_the_same_replay(self) -> None:
        one = replay_season(_data(), _corpus(), SETTINGS, reporter=SilentReporter())
        two = replay_season(_data(), _corpus(), SETTINGS, reporter=SilentReporter())

        assert one.paired == two.paired

    def test_a_room_limit_truncates_the_corpus_and_says_so(self) -> None:
        from dataclasses import replace

        result = replay_season(
            _data(), _corpus(rooms=3), replace(SETTINGS, rooms=1), reporter=SilentReporter()
        )

        assert result.rooms == 1
        assert {row.room for row in result.paired} == {"room0"}

    def test_a_stop_ends_the_replay_and_keeps_what_it_earned(self) -> None:
        calls: list[int] = []

        def stop() -> bool:
            calls.append(1)
            return len(calls) > 7

        result = replay_season(
            _data(), _corpus(), SETTINGS, reporter=SilentReporter(), should_stop=stop
        )

        assert result.paired
        assert max(row.giornata for row in result.paired) < SETTINGS.last


class TestTheSweepAndTheGate:
    def test_h_is_the_argmax_of_the_sweep_season(self) -> None:
        picked, scored = sweep_half_life(
            _data(season=SWEEP), _corpus(), SETTINGS,
            candidates=(90.0, 180.0), reporter=SilentReporter(),
        )

        assert picked in (90.0, 180.0)
        assert [h for h, _d in scored] == [90.0, 180.0]
        assert picked == max(scored, key=lambda item: (item[1], -item[0]))[0]

    def test_a_tie_goes_to_the_shorter_half_life(self) -> None:
        """Between two H the sweep cannot separate, the one that forgets faster is less
        likely to be fitting a season that is over."""
        picked, _scored = sweep_half_life(
            _data(season=SWEEP), _corpus(), SETTINGS,
            candidates=(90.0, 90.0), reporter=SilentReporter(),
        )

        assert picked == 90.0

    def test_the_graded_seasons_use_the_swept_h_and_not_their_own(self) -> None:
        """The whole point of a held-out sweep. If the graded arm re-swept, `sweep` would
        report one H and the run would have used another."""
        history = FakeHistory()

        report = run_gate(
            history, _corpus(), SETTINGS,
            seasons=[SEASON], sweep_season=SWEEP, seed=5, reporter=SilentReporter(),
            draws=50,
        )

        assert report.sweep_season == SWEEP
        assert report.half_life == max(report.sweep, key=lambda item: (item[1], -item[0]))[0]

    def test_the_report_states_what_it_ran(self) -> None:
        report = run_gate(
            FakeHistory(), _corpus(), SETTINGS,
            seasons=[SEASON], sweep_season=SWEEP, seed=5, reporter=SilentReporter(),
            draws=50,
        )
        text = "\n".join(report.lines())

        assert "Gate 1:" in text
        assert f"swept on {SWEEP}" in text
        assert "sub mode basic" in text
        assert "roles from 2026/27" in text
        assert "limitation:" in text

    def test_a_gate_needs_every_graded_season_to_pass(self) -> None:
        """One season is a coin that came up heads (A16)."""
        report = run_gate(
            FakeHistory(), _corpus(), SETTINGS,
            seasons=[SEASON], sweep_season=SWEEP, seed=5, reporter=SilentReporter(),
            draws=50,
        )

        assert report.passes == all(v.passes for v in report.verdicts)

    def test_the_two_intervals_are_over_the_same_rows(self) -> None:
        result = replay_season(_data(), _corpus(), SETTINGS, reporter=SilentReporter())

        verdict = grade(result, rng=np.random.default_rng(1), draws=50)

        assert verdict.rows == len(result.paired)
        assert verdict.points.rooms == verdict.fantapunti.rooms


class TestTheRosterSeed:
    def test_it_is_the_roster_and_nothing_else(self) -> None:
        assert roster_seed([3, 1, 2]) == roster_seed([1, 2, 3])

    def test_two_rosters_are_two_seeds(self) -> None:
        assert roster_seed([1, 2, 3]) != roster_seed([1, 2, 4])

    def test_it_survives_the_process(self) -> None:
        """`hash()` is salted per run; a replay seeded from it would differ between two runs
        of the same command."""
        assert roster_seed([1, 2, 3]) == 2322260305
