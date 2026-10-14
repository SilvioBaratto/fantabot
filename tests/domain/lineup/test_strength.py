"""Team strength: a club's expected points per game, known strictly before a date. Pure.

Hand-checked on `_league_world`'s four-club league, whose arithmetic is small enough to do
on paper. The properties that matter are the three a leak or a mis-scaling would break: the
squad value is a **mean** over the season's own clubs, a result on or after the cutoff cannot
reach the estimate, and the prior is worth 64 matches.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest
from _league_world import (
    CODES,
    fixtures,
    opens,
    plain_expected,
    plain_map,
    plain_points,
    plain_squad_z,
    valuations,
)

from fantabot.domain.lineup.history import Fixture, Valuation
from fantabot.domain.lineup.strength import (
    PRIOR_MATCHES,
    SeasonTable,
    ValueMap,
    expected_ppg,
    fit_value_map,
    season_table,
)

SEASON = "2024/25"
#: A flat prior of 1.0 point a game, so the record is the only thing that moves an estimate.
FLAT = ValueMap(intercept=1.0, slope=0.0)


def _fx(giornata: int, day: int, home: str, away: str, hg: int, ag: int) -> Fixture:
    return Fixture(
        season=SEASON, giornata=giornata, played_on=date(2024, 9, day),
        home=home, away=away, home_goals=hg, away_goals=ag,
    )


#: Two rounds, by hand. ALP 3+0, BRA 0+1, CHA 1+3 (an away win), DEL 1+1.
RECORD = [
    _fx(1, 1, "Alpha", "Bravo", 2, 0),
    _fx(1, 1, "Charlie", "Delta", 1, 1),
    _fx(2, 8, "Alpha", "Charlie", 0, 1),
    _fx(2, 8, "Bravo", "Delta", 3, 3),
]


class TestSquadValue:
    def test_a_squad_is_its_mean_qi_z_scored_over_the_season_s_clubs(self) -> None:
        table = season_table(SEASON, valuations(), fixtures(SEASON))
        assert table is not None
        means = [10.0, 20.0, 30.0, 40.0]
        centre = sum(means) / 4
        sd = math.sqrt(sum((m - centre) ** 2 for m in means) / 4)
        assert [table.z[c] for c in CODES] == pytest.approx([(m - centre) / sd for m in means])

    def test_the_mean_and_not_the_sum_so_a_deep_bench_buys_no_strength(self) -> None:
        vals = valuations()
        # ALP gets six players at 12 (sum 72, mean 12); BRA keeps two at 20 (sum 40, mean 20).
        vals = {pid: v for pid, v in vals.items() if v.squadra != "ALP"}
        vals |= {500 + k: Valuation(qi=12, squadra="ALP") for k in range(6)}
        table = season_table(SEASON, vals, fixtures(SEASON))
        assert table is not None
        assert table.z["BRA"] > table.z["ALP"]

    def test_a_season_whose_clubs_are_all_equal_has_no_table(self) -> None:
        flat = {pid: Valuation(qi=20, squadra=v.squadra) for pid, v in valuations().items()}
        assert season_table(SEASON, flat, fixtures(SEASON)) is None

    def test_one_club_is_not_a_league(self) -> None:
        assert season_table(SEASON, {1: Valuation(qi=20, squadra="ALP")}, []) is None

    def test_a_season_with_no_valuations_at_all_has_no_table(self) -> None:
        assert season_table(SEASON, {}, fixtures(SEASON)) is None

    def test_before_the_first_match_a_club_has_a_squad_value_and_no_record(self) -> None:
        table = season_table(SEASON, valuations(), [])
        assert table is not None
        assert set(table.z) == set(CODES)
        for club in CODES:
            assert expected_ppg(table, ValueMap(1.3, 0.2), club, before=date(2024, 9, 1)) == (
                pytest.approx(1.3 + 0.2 * table.z[club])
            )


class TestTheRecord:
    def test_points_are_three_a_win_one_a_draw_nothing_a_loss_home_or_away(self) -> None:
        table = season_table(SEASON, valuations(), RECORD)
        assert table is not None
        got = {
            c: expected_ppg(table, FLAT, c, before=date(2024, 9, 9), prior_matches=4.0)
            for c in CODES
        }
        # (4 * 1.0 + points) / (4 + 2 matches)
        assert got == pytest.approx(
            {"ALP": 7 / 6, "BRA": 5 / 6, "CHA": 8 / 6, "DEL": 6 / 6}
        )

    def test_a_match_on_the_cutoff_day_is_not_known_yet(self) -> None:
        table = season_table(SEASON, valuations(), RECORD)
        assert table is not None
        # Round 2 is dated 8 September. Cut on it, only round 1 is history: ALP 3 over 1.
        assert expected_ppg(table, FLAT, "ALP", before=date(2024, 9, 8), prior_matches=4.0) == (
            pytest.approx((4.0 + 3.0) / 5.0)
        )
        # And the day after, round 2 is in.
        assert expected_ppg(table, FLAT, "ALP", before=date(2024, 9, 9), prior_matches=4.0) == (
            pytest.approx((4.0 + 3.0 + 0.0) / 6.0)
        )

    def test_with_nothing_played_before_the_cutoff_it_is_exactly_the_prior(self) -> None:
        table = season_table(SEASON, valuations(), RECORD)
        assert table is not None
        assert expected_ppg(table, FLAT, "ALP", before=date(2024, 9, 1)) == 1.0

    def test_a_duplicated_fixture_row_counts_once(self) -> None:
        once = season_table(SEASON, valuations(), RECORD)
        twice = season_table(SEASON, valuations(), [*RECORD, RECORD[0]])
        assert once is not None and twice is not None
        for club in CODES:
            assert expected_ppg(twice, FLAT, club, before=date(2024, 9, 9)) == (
                expected_ppg(once, FLAT, club, before=date(2024, 9, 9))
            )

    def test_an_unknown_club_has_no_strength(self) -> None:
        table = season_table(SEASON, valuations(), RECORD)
        assert table is not None
        assert expected_ppg(table, FLAT, "XXX", before=date(2024, 9, 9)) is None


class TestAPostponedMatchIsDatedWhereItWasPlayed:
    """A giornata is not a date: a postponed match keeps its round and is played after later
    ones. The rows below come in giornata order, so ALP's dates arrive as the 1st, the 20th and
    then the 15th, and a record that bisected them as they came would count the wrong matches."""

    #: ALP-CHA, round 2, was moved from the 8th to the 20th, after round 3 on the 15th.
    POSTPONED = (
        _fx(1, 1, "Alpha", "Bravo", 2, 0),
        _fx(1, 1, "Charlie", "Delta", 1, 1),
        _fx(2, 20, "Alpha", "Charlie", 0, 1),
        _fx(2, 8, "Bravo", "Delta", 3, 3),
        _fx(3, 15, "Alpha", "Delta", 2, 1),
        _fx(3, 15, "Bravo", "Charlie", 1, 0),
    )

    def test_a_club_s_dates_and_running_points_follow_the_calendar(self) -> None:
        table = season_table(SEASON, valuations(), self.POSTPONED)
        assert table is not None
        assert table.dates["ALP"] == (date(2024, 9, 1), date(2024, 9, 15), date(2024, 9, 20))
        # 3 on the 1st, 3 on the 15th, then the postponed loss on the 20th.
        assert table.running["ALP"] == (0.0, 3.0, 6.0, 6.0)

    def test_by_hand_on_the_16th_alp_has_played_twice_and_taken_six(self) -> None:
        table = season_table(SEASON, valuations(), self.POSTPONED)
        assert table is not None
        got = expected_ppg(table, FLAT, "ALP", before=date(2024, 9, 16), prior_matches=4.0)
        assert got == pytest.approx((4.0 + 6.0) / (4.0 + 2.0))

    @pytest.mark.parametrize("day", [2, 9, 16, 21])
    def test_every_club_agrees_with_the_independent_loop_at_every_cutoff(self, day: int) -> None:
        """`plain_points` filters by date and never orders anything, so it cannot share a
        mistake with a record that has to be put in order."""
        table = season_table(SEASON, valuations(), self.POSTPONED)
        assert table is not None
        cut = date(2024, 9, day)
        points = plain_points(self.POSTPONED)
        for club in CODES:
            earned = [p for d, p in points[club] if d < cut]
            want = (4.0 * 1.0 + sum(earned)) / (4.0 + len(earned))
            assert expected_ppg(table, FLAT, club, before=cut, prior_matches=4.0) == (
                pytest.approx(want)
            )

    def test_the_order_the_rows_arrive_in_changes_nothing(self) -> None:
        forward = season_table(SEASON, valuations(), self.POSTPONED)
        backward = season_table(SEASON, valuations(), list(reversed(self.POSTPONED)))
        assert forward is not None and backward is not None
        assert forward.dates == backward.dates and forward.running == backward.running


class TestAFixtureForAClubTheListoneDoesNotHold:
    """A club code that `quotazioni` and `match_grain` disagree on, or a promoted side the
    listone has not caught up with. It must cost that season's training and never raise: the
    table is built on the hourly job's path with the setting on."""

    STRAY = _fx(1, 3, "Alpha", "Zulu", 2, 0)

    def _table(self) -> SeasonTable:
        table = season_table(SEASON, valuations(), [*RECORD, self.STRAY])
        assert table is not None
        return table

    def test_it_adds_no_club_and_does_not_raise(self) -> None:
        table = self._table()
        assert set(table.z) == set(CODES)
        assert set(table.dates) == set(CODES) and set(table.running) == set(CODES)
        assert expected_ppg(table, FLAT, "ZUL", before=date(2024, 9, 9)) is None

    def test_the_listed_club_still_counts_the_match(self) -> None:
        table = self._table()
        plain = season_table(SEASON, valuations(), RECORD)
        assert plain is not None
        cut = date(2024, 9, 9)
        # ALP's three points against the unlisted side join its record of 3 + 0.
        assert expected_ppg(table, FLAT, "ALP", before=cut, prior_matches=4.0) == (
            pytest.approx((4.0 + 3.0 + 0.0 + 3.0) / 7.0)
        )
        for club in ("BRA", "CHA", "DEL"):
            assert expected_ppg(table, FLAT, club, before=cut) == (
                expected_ppg(plain, FLAT, club, before=cut)
            )

    def test_such_a_season_is_not_a_complete_one_and_so_does_not_train_the_map(self) -> None:
        """The control is the same season without the stray row: complete, and it trains."""
        clean = season_table(SEASON, valuations(), fixtures(SEASON))
        stray = season_table(SEASON, valuations(), [*fixtures(SEASON), self.STRAY])
        other = season_table("2022/23", valuations(), fixtures("2022/23", shift=1))
        assert clean is not None and stray is not None and other is not None
        assert clean.complete and not stray.complete
        assert fit_value_map([other, stray]) == fit_value_map([other])
        assert fit_value_map([other, clean]) != fit_value_map([other])


class TestNothingFromTheFutureReachesTheEstimate:
    @pytest.mark.parametrize("giornata", [1, 2, 3, 4, 5, 6])
    def test_scrambling_every_result_on_or_after_the_cutoff_moves_nothing(
        self, giornata: int
    ) -> None:
        """The scramble turns every result into a different *kind* of result (a win into a
        draw or a loss, a draw into a win, a loss into a win), so no match is left alone."""
        fx = fixtures(SEASON)
        cutoff = min(f.played_on for f in fx if f.giornata == giornata)
        scrambled = [
            replace(f, home_goals=f.away_goals + 1, away_goals=f.home_goals)
            if f.played_on >= cutoff
            else f
            for f in fx
        ]
        real, other = (season_table(SEASON, valuations(), x) for x in (fx, scrambled))
        assert real is not None and other is not None
        vmap = ValueMap(1.3, 0.2)
        for club in CODES:
            assert expected_ppg(other, vmap, club, before=cutoff) == (
                expected_ppg(real, vmap, club, before=cutoff)
            )

    def test_the_estimate_does_move_when_a_result_before_the_cutoff_changes(self) -> None:
        """The control: the perturbation above has teeth only if history *can* move it."""
        fx = fixtures(SEASON)
        cutoff = min(f.played_on for f in fx if f.giornata == 4)
        changed = [replace(fx[0], home_goals=0, away_goals=3), *fx[1:]]  # ALP's win becomes a loss
        real, other = (season_table(SEASON, valuations(), x) for x in (fx, changed))
        assert real is not None and other is not None
        club = CODES[0]
        assert expected_ppg(other, FLAT, club, before=cutoff) != (
            expected_ppg(real, FLAT, club, before=cutoff)
        )


class TestACompleteSeason:
    def test_a_double_round_robin_is_complete(self) -> None:
        table = season_table(SEASON, valuations(), fixtures(SEASON))
        assert table is not None and table.complete

    def test_a_season_missing_a_round_is_not(self) -> None:
        table = season_table(SEASON, valuations(), fixtures(SEASON, rounds=5))
        assert table is not None and not table.complete

    def test_nor_is_one_with_a_club_that_has_no_fixtures(self) -> None:
        table = season_table(SEASON, valuations(), [])
        assert table is not None and not table.complete

    def test_a_duplicated_row_does_not_make_a_club_play_seven_matches(self) -> None:
        fx = fixtures(SEASON)
        table = season_table(SEASON, valuations(), [*fx, fx[0]])
        assert table is not None and table.complete


def _seasons(*names: str) -> dict[str, tuple[dict[int, Valuation], list[Fixture]]]:
    return {n: (valuations(), fixtures(n, shift=i)) for i, n in enumerate(names)}


class TestTheValueMap:
    def test_it_is_the_least_squares_line_of_final_points_per_game_on_z(self) -> None:
        world = _seasons("2022/23", "2023/24")
        tables = [season_table(s, v, f) for s, (v, f) in world.items()]
        assert all(t is not None for t in tables)
        fitted = fit_value_map([t for t in tables if t is not None])
        assert fitted is not None
        intercept, slope = plain_map(
            {s: v for s, (v, _) in world.items()}, {s: f for s, (_, f) in world.items()},
            list(world),
        )
        assert fitted.intercept == pytest.approx(intercept)
        assert fitted.slope == pytest.approx(slope)

    def test_an_incomplete_season_does_not_train_it(self) -> None:
        full = season_table("2022/23", valuations(), fixtures("2022/23"))
        part = season_table("2023/24", valuations(), fixtures("2023/24", shift=1, rounds=4))
        assert full is not None and part is not None
        assert fit_value_map([full, part]) == fit_value_map([full])

    def test_with_no_complete_season_there_is_no_map(self) -> None:
        part = season_table("2023/24", valuations(), fixtures("2023/24", rounds=4))
        assert part is not None
        assert fit_value_map([]) is None
        assert fit_value_map([part]) is None

    def test_a_map_that_says_stronger_squads_take_fewer_points_is_refused(self) -> None:
        # The lower-indexed (cheaper) club always wins, so points fall as squad value rises.
        def cheap_wins(_: int, home: int, away: int) -> tuple[int, int]:
            return (1, 0) if home < away else (0, 1)

        inverted = season_table(
            "2022/23", valuations(), fixtures("2022/23", result=cheap_wins)
        )
        assert inverted is not None and inverted.complete
        assert fit_value_map([inverted]) is None


class TestTheBlend:
    def test_the_prior_weighs_sixty_four_matches(self) -> None:
        assert PRIOR_MATCHES == 64.0

    def test_it_agrees_with_the_independent_loop_for_every_club_and_giornata(self) -> None:
        names = ("2022/23", "2023/24", "2024/25")
        vals = {n: valuations() for n in names}
        fx = {n: fixtures(n, shift=i) for i, n in enumerate(names)}
        tables = {n: season_table(n, vals[n], fx[n]) for n in names}
        trained = fit_value_map([t for n, t in tables.items() if n != "2024/25" and t])
        assert trained is not None and tables["2024/25"] is not None
        for g in range(1, 7):
            cutoff = opens(fx["2024/25"], g)
            for club in CODES:
                assert expected_ppg(tables["2024/25"], trained, club, before=cutoff) == (
                    pytest.approx(plain_expected(
                        vals, fx, map_seasons=["2022/23", "2023/24"], season="2024/25",
                        club=club, before=cutoff,
                    ))
                )

    def test_a_shorter_prior_lets_the_record_count_for_more(self) -> None:
        table = season_table(SEASON, valuations(), RECORD)
        assert table is not None
        cut = date(2024, 9, 9)
        slow = expected_ppg(table, ValueMap(1.0, 0.0), "CHA", before=cut, prior_matches=64.0)
        quick = expected_ppg(table, ValueMap(1.0, 0.0), "CHA", before=cut, prior_matches=2.0)
        assert slow is not None and quick is not None
        assert abs(quick - 2.0) < abs(slow - 2.0)  # CHA's record is 2 points a game


def test_the_z_scores_of_a_season_are_the_independent_ones() -> None:
    table = season_table(SEASON, valuations(), fixtures(SEASON))
    assert table is not None
    assert dict(table.z) == pytest.approx(plain_squad_z(valuations()))
