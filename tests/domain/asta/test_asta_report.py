"""Pure helpers for the offline asta CLI: input parsing, input assembly, rendering.

The CLI commands themselves touch the database and are smoke-tested against it; everything
worth a unit test is pure and lives here.
"""

from __future__ import annotations

from typing import ClassVar

from fantabot.domain.asta.report import (
    build_pool,
    build_value,
    format_legality,
    format_roster,
    parse_ids,
    parse_replay_lines,
)
from fantabot.domain.asta.state import Roster


def test_parse_ids_splits_on_commas_and_whitespace() -> None:
    assert parse_ids("1, 2  3,4") == ("1", "2", "3", "4")
    assert parse_ids("  ") == ()


def test_build_pool_normalizes_roles() -> None:
    pool = build_pool({"1": ["Dc"], "2": ["a", "w"]})
    by_id = {p.id: p for p in pool}
    assert by_id["1"].roles == frozenset({"DC"})
    assert by_id["2"].roles == frozenset({"A", "W"})


def test_build_value_marks_unpriced_players_as_no_history() -> None:
    model = build_value({"1": 30, "2": 5}, priced_ids={"1"})
    assert model.value("1").mean == 30.0
    # "2" has an fvm but was never sold in the loaded aste → wider band
    assert model.value("2").variance == model.no_history_variance
    assert model.value("1").variance == model.base_variance


def test_format_roster_shows_cost_and_names() -> None:
    roster = Roster(player_ids=("1", "2"), total_cost=42.0, objective=53.0)
    text = format_roster(roster, names={"1": "Malen", "2": "Zaccagni"}, prices={"1": 30.0, "2": 12.0})
    assert "Malen" in text
    assert "42" in text
    assert "obj" in text.lower()


def test_format_legality_reports_fieldable_and_none() -> None:
    assert "3-4-3" in format_legality(frozenset({"3-4-3", "4-3-3"}))
    assert "no legal xi" in format_legality(frozenset()).lower()


def test_parse_replay_lines_skips_malformed_lines() -> None:
    lines = ['{"a": 1}', "not json", "", '{"b": 2}']
    assert parse_replay_lines(lines) == [{"a": 1}, {"b": 2}]



class TestTheRosterNeverShowsAPriceYouCannotPay:
    """The platform's minimum bid is 1 credit. A line reading `0` is not a plan.

    `format_roster` printed `prices.get(player_id, 0.0)`, so a player with no observed
    clearing sale showed as costing nothing -- while the optimizer had budgeted the
    1-credit riserva for him all along (`optimizer._cost` is `max(DEFAULT_PRICE, ...)`).
    The report and the plan disagreed, and the report was the half an operator reads.

    It showed up on five players across the golden cases once the unsold lots stopped
    feeding the price model: before that, an unpriced player was rare because almost
    everyone had *some* row.
    """

    ROSTER = Roster(player_ids=("1", "2", "3"), total_cost=42, objective=1.0)
    NAMES: ClassVar[dict[str, str]] = {
        "1": "Priced", "2": "Unpriced", "3": "Rounds to nothing"
    }

    def _lines(self, prices: dict[str, float]) -> list[str]:
        return format_roster(self.ROSTER, self.NAMES, prices).splitlines()[1:]

    def test_a_player_with_no_observed_sale_shows_the_riserva_price(self) -> None:
        lines = self._lines({"1": 40.0})

        assert "Unpriced" in lines[1]
        assert lines[1].split()[-1] == "1"

    def test_a_price_below_one_credit_is_never_shown_as_zero(self) -> None:
        """Cannot arise from real data now, but the floor is the platform's, not the data's."""
        assert self._lines({"1": 40.0, "3": 0.4})[2].split()[-1] == "1"

    def test_an_observed_price_is_shown_as_observed(self) -> None:
        assert self._lines({"1": 40.0})[0].split()[-1] == "40"

    def test_the_lines_sum_to_what_the_header_claims(self) -> None:
        """The header's `cost` comes from the optimizer; the lines came from the raw map,
        so an unpriced player made the two disagree by a credit each."""
        prices = {"1": 40.0}
        roster = Roster(player_ids=("1", "2", "3"), total_cost=42, objective=1.0)
        lines = format_roster(roster, self.NAMES, prices).splitlines()

        assert sum(int(line.split()[-1]) for line in lines[1:]) == roster.total_cost
