"""Is the history fresh enough to project on? Pure. SPEC A20.

Two real shapes anchor it. **2024/25 giornata 9** closed with 9 of its 10 matches, the tenth
postponed; **2025/26 giornata 16** had 6 of 10 on the day giornata 17 opened, the other four
played on 14-15 January. Both are *fresh* — a postponed match is a warning, never a fallback
— provided the voti were scraped after the previous round was calculated.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lega.models import Fixture as LegaFixture
from fantabot.domain.lineup.freshness import (
    SERIE_A_MATCHES,
    VOTI_GETS_PER_RUN,
    previous_round_calculated,
    staleness,
    voti_range,
)


def _round(giornata: int, *, calculated: bool) -> list[LegaFixture]:
    """The lega's four matches of the round played on Serie A's `giornata`."""
    return [
        LegaFixture(
            competition_id=1, matchday=giornata - 2, championship_matchday=giornata,
            team_home=home, team_away=home + 10, points_home=None, points_away=None,
            standing_home=None, standing_away=None, result=None, real_result=None,
            calculated=calculated,
        )
        for home in range(4)
    ]


def _counts(last: int, **short: int) -> dict[int, int]:
    """Matches recorded per giornata 1..last: 10 each, except `g<n>=<count>`."""
    out = {g: SERIE_A_MATCHES for g in range(1, last + 1)}
    out.update({int(key[1:]): count for key, count in short.items()})
    return out


class TestPreviousRoundCalculated:
    def test_the_previous_round_calculated_is_true(self) -> None:
        calendar = [*_round(3, calculated=True), *_round(4, calculated=False)]
        assert previous_round_calculated(calendar, cmday=4) is True

    def test_the_previous_round_not_yet_calculated_is_false(self) -> None:
        calendar = [*_round(3, calculated=False), *_round(4, calculated=False)]
        assert previous_round_calculated(calendar, cmday=4) is False

    def test_the_current_round_does_not_count(self) -> None:
        calendar = [*_round(3, calculated=False), *_round(4, calculated=True)]
        assert previous_round_calculated(calendar, cmday=4) is False

    def test_a_round_only_partly_calculated_is_not_calculated(self) -> None:
        mixed = _round(3, calculated=True)[:2] + _round(3, calculated=False)[2:]
        assert previous_round_calculated(mixed, cmday=4) is False

    def test_no_lega_round_on_the_previous_giornata_is_false(self) -> None:
        """This lega's matchday 1 is Serie A's 3: nothing on the calendar can vouch for g2,
        so the refresh does not run and the projection falls back — fail closed."""
        assert previous_round_calculated(_round(3, calculated=False), cmday=3) is False


class TestStaleness:
    def test_2024_25_g9_with_a_postponed_match_is_fresh_with_a_warning(self) -> None:
        verdict = staleness(
            max_giornata=9, cmday=10, fixtures_per_giornata=_counts(9, g9=9),
            voti_refreshed=True,
        )
        assert verdict.fresh
        assert verdict.reasons == ()
        assert verdict.warnings == ("g9 9/10 (postponed?)",)

    def test_2025_26_g16_with_four_postponed_is_fresh(self) -> None:
        verdict = staleness(
            max_giornata=16, cmday=17, fixtures_per_giornata=_counts(16, g16=6),
            voti_refreshed=True,
        )
        assert verdict.fresh
        assert verdict.warnings == ("g16 6/10 (postponed?)",)

    def test_the_same_g16_without_a_voti_refresh_is_stale_and_says_why(self) -> None:
        verdict = staleness(
            max_giornata=16, cmday=17, fixtures_per_giornata=_counts(16, g16=6),
            voti_refreshed=False,
        )
        assert not verdict.fresh
        assert len(verdict.reasons) == 1
        assert "voti" in verdict.reasons[0] and "g16" in verdict.reasons[0]

    def test_history_short_of_the_previous_giornata_is_stale(self) -> None:
        verdict = staleness(
            max_giornata=15, cmday=17, fixtures_per_giornata=_counts(15), voti_refreshed=True
        )
        assert not verdict.fresh
        assert len(verdict.reasons) == 1
        assert "g15" in verdict.reasons[0] and "g16" in verdict.reasons[0]

    def test_both_reasons_are_given(self) -> None:
        verdict = staleness(
            max_giornata=15, cmday=17, fixtures_per_giornata=_counts(15), voti_refreshed=False
        )
        assert not verdict.fresh
        assert len(verdict.reasons) == 2

    def test_a_giornata_missing_in_the_middle_is_a_warning(self) -> None:
        counts = _counts(9)
        del counts[5]
        verdict = staleness(
            max_giornata=9, cmday=10, fixtures_per_giornata=counts, voti_refreshed=True
        )
        assert verdict.fresh
        assert verdict.warnings == ("g5 0/10 (postponed?)",)

    def test_complete_history_has_no_warning(self) -> None:
        verdict = staleness(
            max_giornata=9, cmday=10, fixtures_per_giornata=_counts(9), voti_refreshed=True
        )
        assert verdict.fresh
        assert verdict.warnings == ()

    def test_the_giornata_being_played_is_not_short(self) -> None:
        """g10 is under way: its 3 matches so far are not a warning about history."""
        verdict = staleness(
            max_giornata=10, cmday=10, fixtures_per_giornata=_counts(10, g10=3),
            voti_refreshed=True,
        )
        assert verdict.warnings == ()


class TestVotiRange:
    def test_every_short_giornata_before_this_one_ascending(self) -> None:
        assert voti_range(cmday=17, fixtures_per_giornata=_counts(16, g16=6, g12=9)) == [12, 16]

    def test_a_missing_giornata_is_short(self) -> None:
        assert voti_range(cmday=5, fixtures_per_giornata={1: 10, 3: 10}) == [2, 4]

    def test_it_is_capped_per_run(self) -> None:
        assert VOTI_GETS_PER_RUN == 8
        assert voti_range(cmday=20, fixtures_per_giornata={}) == list(range(1, 9))

    def test_nothing_short_is_nothing_to_fetch(self) -> None:
        assert voti_range(cmday=10, fixtures_per_giornata=_counts(9)) == []

    def test_the_giornata_being_played_is_not_fetched(self) -> None:
        assert voti_range(cmday=10, fixtures_per_giornata=_counts(10, g10=3)) == []

    @pytest.mark.parametrize("cmday", [0, 1])
    def test_before_giornata_2_there_is_nothing_to_fetch(self, cmday: int) -> None:
        assert voti_range(cmday=cmday, fixtures_per_giornata={}) == []
