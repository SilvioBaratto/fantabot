"""Pure helpers for the offline asta CLI: input parsing, input assembly, rendering.

The CLI commands themselves touch the database and are smoke-tested against it; everything
worth a unit test is pure and lives here.
"""

from __future__ import annotations

from fantabot.asta_engine.report import (
    build_pool,
    build_value,
    format_legality,
    format_roster,
    parse_ids,
)
from fantabot.asta_engine.state import Roster


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
