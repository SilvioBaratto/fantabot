"""The history value types, the date cutoff and side resolution. **Literal rows only.**

The fixtures are real: `match_grain`'s 2025/26 giornate 16 and 17. Four giornata-16 matches
were postponed to 14-15 January 2026 — after giornate 17, 18 and 19 had started — and kept
their giornata, which is what makes a cutoff by giornata leak (SPEC A9).
"""

from __future__ import annotations

from datetime import date

import pytest

from fantabot.domain.lineup.history import (
    Fixture,
    HistoryAppearance,
    before,
    first_match_date,
    plays_in,
    side,
)
from fantabot.domain.lineup.scoring import Appearance

SEASON = "2025/26"


def _fixture(giornata: int, played_on: date, home: str, away: str) -> Fixture:
    return Fixture(
        season=SEASON, giornata=giornata, played_on=played_on,
        home=home, away=away, home_goals=0, away_goals=0,
    )


#: 2025/26 giornata 16 as played: six matches in December, four postponed to January.
G16 = [
    _fixture(16, date(2025, 12, 20), "Juventus", "Roma"),
    _fixture(16, date(2025, 12, 20), "Lazio", "Cremonese"),
    _fixture(16, date(2025, 12, 21), "Cagliari", "Pisa"),
    _fixture(16, date(2025, 12, 21), "Fiorentina", "Udinese"),
    _fixture(16, date(2025, 12, 21), "Genoa", "Atalanta"),
    _fixture(16, date(2025, 12, 21), "Sassuolo", "Torino"),
    _fixture(16, date(2026, 1, 14), "Inter", "Lecce"),
    _fixture(16, date(2026, 1, 14), "Napoli", "Parma"),
    _fixture(16, date(2026, 1, 15), "Como", "Milan"),
    _fixture(16, date(2026, 1, 15), "Verona", "Bologna"),
]
#: 2025/26 giornata 17, which opened on 27 December.
G17 = [
    _fixture(17, date(2025, 12, 27), "Lecce", "Como"),
    _fixture(17, date(2025, 12, 27), "Parma", "Fiorentina"),
    _fixture(17, date(2025, 12, 28), "Atalanta", "Inter"),
    _fixture(17, date(2025, 12, 29), "Roma", "Genoa"),
]


def _played(player_id: int, fixture: Fixture) -> HistoryAppearance:
    return HistoryAppearance(
        player_id=player_id, role="D", fixture=fixture,
        scored=Appearance(voto_fc=6.0), fantavoto_fc=6.0,
    )


def test_a_giornata_starts_on_its_first_match_date_not_on_its_postponed_ones() -> None:
    assert first_match_date([*G16, *G17], season=SEASON, giornata=17) == date(2025, 12, 27)
    assert first_match_date([*G16, *G17], season=SEASON, giornata=16) == date(2025, 12, 20)


def test_a_giornata_with_no_fixture_is_refused() -> None:
    with pytest.raises(ValueError, match="18"):
        first_match_date(G16, season=SEASON, giornata=18)


def test_the_cutoff_is_by_date_so_a_postponed_match_stays_out() -> None:
    """At giornata 17, Inter-Lecce is giornata 16 — and was played on 14 January, after
    giornata 17 itself. A filter on `giornata < 17` would hand the model a result from the
    future; the date filter does not."""
    regular = _played(1, G16[0])  # 20 December
    postponed = _played(2, G16[6])  # Inter-Lecce, 14 January

    kept = before([regular, postponed], first_match_date([*G16, *G17], season=SEASON, giornata=17))

    assert kept == [regular]
    assert postponed.fixture.giornata < 17, "the leak a giornata filter would let through"


def test_an_appearance_on_the_giornata_s_own_first_date_is_excluded() -> None:
    """27 December is giornata 17: its matches are what is being predicted, not history."""
    opening_day = _played(3, G17[0])

    assert before([opening_day], date(2025, 12, 27)) == []
    assert before([opening_day], date(2025, 12, 28)) == [opening_day]


class TestSide:
    """`match_grain.squadra_raw` is the **home** team on both sides' rows, so a player's
    side comes from his own club code (`quotazioni.squadra`), matched on either end."""

    def test_home_away_and_neither(self) -> None:
        inter_lecce = G16[6]

        assert side(inter_lecce, "INT") == "home"
        assert side(inter_lecce, "LEC") == "away"
        assert side(inter_lecce, "NAP") is None

    def test_participation_is_home_or_away(self) -> None:
        inter_lecce = G16[6]

        assert plays_in(inter_lecce, "INT") and plays_in(inter_lecce, "LEC")
        assert not plays_in(inter_lecce, "NAP")
