"""T24: fetching a named handful of giornate, for a job nobody is watching.

`voti.run` is the operator's command: it walks a whole season, prints as it goes and exits
1 when the site gave it nothing, because a page-shape change is what that usually means.
None of those three is right for the hourly refresh (T27), whose whole purpose is to try a
postponed giornata again next hour — so `scrape_giornate` is the same fetch with the
printing, the exiting and the whole-season walk taken out, and counts returned instead.

Nothing here opens a socket or a session: the fetch, the store and the politeness sleep are
injected. The sleep especially — the test asserts the delay is taken *between* giornate, and
a real one would cost 2 s a pair to say so.

⚠ **The rows below are hand-built and one of their shapes does not occur.** `_row` gives
every team its own name, so `test_a_played_giornata_counts_both_sides_of_every_match` feeds
20 distinct teams — and the real parser never produces 20. It records both tables of a match
under the *home* side, so a played giornata yields 10, and `fixtures` reads 5 rather than 10.
That is a defect in `GiornataParser`, not in `count_giornata`, and it is pinned against the
recorded page in `test_voti_parser.py::TestTheMatchHeader`. Kept as it is here because these
tests are about the counting rule in isolation; the note is so the 20 is not read as evidence
that the page delivers one.
"""

from __future__ import annotations

import pytest

from fantabot.adapters.scraping import voti
from fantabot.adapters.scraping.voti import (
    GiornataCount,
    PlayerMatchRow,
    count_giornata,
    scrape_giornate,
)


def _row(team: str, *, voto: str = "6", source: str = "voto_fc") -> PlayerMatchRow:
    row = PlayerMatchRow(season="2026/27", giornata=1, team=team)
    setattr(row, source, voto)
    return row


class TestTheCounts:
    def test_a_played_giornata_counts_both_sides_of_every_match(self) -> None:
        rows = [_row(f"team{i}") for i in range(20)]

        count = count_giornata(3, rows, len(rows))

        assert (count.teams_listed, count.teams_graded, count.rows) == (20, 20, 20)
        assert count.fixtures == 10

    def test_a_postponed_match_is_listed_and_not_graded(self) -> None:
        """The site renders a team's table as soon as the fixture exists and fills the
        grades in when it has been played. That gap is the whole of `freshness`'s "6/10"."""
        rows = [_row(f"team{i}") for i in range(18)] + [
            _row("late-a", voto=""), _row("late-b", voto=""),
        ]

        count = count_giornata(16, rows, len(rows))

        assert (count.teams_listed, count.teams_graded) == (20, 18)
        assert count.fixtures == 9

    @pytest.mark.parametrize("source", ["voto_fc", "voto_stat", "voto_italia"])
    def test_any_of_the_three_sources_grades_a_team(self, source: str) -> None:
        """Voto Italia can publish before the Redazione does. A giornata read as ungraded
        is one the refresh re-fetches for ever, so the test is over all three."""
        count = count_giornata(1, [_row("solo", source=source)], 1)

        assert count.teams_graded == 1

    def test_a_row_with_no_team_is_counted_nowhere(self) -> None:
        """Coach rows carry no player id and, on some pages, no team either."""
        count = count_giornata(1, [_row(""), _row("real")], 2)

        assert (count.teams_listed, count.teams_graded) == (1, 1)

    def test_an_empty_page_is_zero_and_not_an_error(self) -> None:
        count = count_giornata(9, [], 0)

        assert (count.teams_listed, count.teams_graded, count.rows) == (0, 0, 0)
        assert count.fixtures == 0


class TestTheWalk:
    def test_only_the_requested_giornate_are_fetched(self) -> None:
        asked: list[int] = []

        scrape_giornate(
            "2026/27", [5, 2],
            fetch=lambda _s, g: (asked.append(g), [])[1],
            store=lambda rows: len(rows),
            sleep=lambda _s: None,
        )

        assert asked == [2, 5]

    def test_a_giornata_asked_for_twice_is_fetched_once(self) -> None:
        asked: list[int] = []

        scrape_giornate(
            "2026/27", [4, 4, 4],
            fetch=lambda _s, g: (asked.append(g), [])[1],
            store=lambda rows: len(rows),
            sleep=lambda _s: None,
        )

        assert asked == [4]

    def test_zero_rows_returns_zero_and_does_not_exit(self) -> None:
        """`run` exits 1 on an empty season and is right to. Here an empty giornata is the
        ordinary case — a postponed match — and exiting would end the hourly job."""
        counts = scrape_giornate(
            "2026/27", [7],
            fetch=lambda _s, _g: [],
            store=lambda rows: len(rows),
            sleep=lambda _s: None,
        )

        assert counts == [GiornataCount(giornata=7, teams_listed=0, teams_graded=0, rows=0)]

    def test_the_politeness_delay_is_between_giornate_and_not_before_the_first(self) -> None:
        slept: list[float] = []

        scrape_giornate(
            "2026/27", [1, 2, 3],
            fetch=lambda _s, _g: [],
            store=lambda rows: len(rows),
            sleep=slept.append,
        )

        assert slept == [voti.REQUEST_DELAY_SECONDS, voti.REQUEST_DELAY_SECONDS]

    def test_what_the_store_returns_is_what_is_reported(self) -> None:
        """`store_giornata` upserts and returns what it wrote; the count is not `len(rows)`
        read a second time, or a re-run that stored nothing would report a full giornata."""
        counts = scrape_giornate(
            "2026/27", [1],
            fetch=lambda _s, _g: [_row("a"), _row("b")],
            store=lambda _rows: 0,
            sleep=lambda _s: None,
        )

        assert counts[0].rows == 0
        assert counts[0].teams_listed == 2

    def test_nothing_asked_for_fetches_nothing(self) -> None:
        asked: list[int] = []

        counts = scrape_giornate(
            "2026/27", [],
            fetch=lambda _s, g: (asked.append(g), [])[1],
            store=lambda rows: len(rows),
            sleep=lambda _s: None,
        )

        assert (counts, asked) == ([], [])
