"""Merging what a scan just saw into what we already knew.

Two shapes reach this: the list-of-lists the poller-era scan wrote, and the
objects `GET /fantaleagues/live` returns. Both become one value type here, so
nothing downstream has to know which era a row came from.

The behaviour that matters is what merging must *not* do. A scan reports what is
live now; an auction missing from it has ended, not stopped existing. Dropping
it would lose the configuration needed to interpret events already collected
from it — and those events are on disk, unrepeatable.
"""

from __future__ import annotations

import pytest

from fantabot.domain.harvest.registry import from_card, from_seed_row, merge, to_seed_rows

SEED_ROW = ["a-1", "15", 8, 500, 25, 25, "random", "free", 10, 20, "Lega"]


def _card(auction_id: str, **over: object) -> dict[str, object]:
    card: dict[str, object] = {
        "fantaleague_id": auction_id,
        "db": 15,
        "asta_type": "mantra",
        "fantaleague_name": "Lega",
        "num_teams": 8,
        "num_credits": 500,
        "min_player": 25,
        "max_player": 25,
        "asta_mode": "random",
        "raise_mode": "free",
        "counter_time": 10,
        "counter_time_first": 20,
    }
    card.update(over)
    return card


def test_a_card_becomes_a_config() -> None:
    config = from_card(_card("a-1"))
    assert config.auction_id == "a-1"
    assert config.db_shard == "15", "the shard is a string; the API sends it as a number"
    assert config.asta_type == "mantra"
    assert (config.num_teams, config.num_credits) == (8, 500)


def test_a_legacy_seed_row_becomes_the_same_config() -> None:
    """The poller-era file predates storing the format, so it has to be told."""
    assert from_seed_row(SEED_ROW, asta_type="mantra") == from_card(_card("a-1"))


def test_merging_adds_what_is_new() -> None:
    known = [from_card(_card("a-1"))]
    merged = merge(known, [from_card(_card("a-2"))])
    assert {c.auction_id for c in merged} == {"a-1", "a-2"}


def test_an_auction_missing_from_a_scan_is_not_dropped() -> None:
    """A scan says what is live now. An auction absent from it has ended — and
    its events are already on disk, so losing the configuration that explains
    them would make those events uninterpretable."""
    known = [from_card(_card("a-1")), from_card(_card("a-2"))]
    merged = merge(known, [from_card(_card("a-2"))])
    assert {c.auction_id for c in merged} == {"a-1", "a-2"}


def test_a_rescan_updates_what_changed() -> None:
    known = [from_card(_card("a-1", num_credits=500))]
    merged = merge(known, [from_card(_card("a-1", num_credits=1000))])
    assert [c.num_credits for c in merged] == [1000]


def test_merging_the_same_scan_twice_changes_nothing() -> None:
    known = [from_card(_card("a-1"))]
    scan = [from_card(_card("a-2"))]
    once = merge(known, scan)
    assert merge(once, scan) == once


def test_the_result_is_ordered_so_a_seed_file_diff_stays_readable() -> None:
    """The seed is written back to disk and read by a human when something looks
    wrong. Set iteration order would make every rescan look like a rewrite."""
    merged = merge([], [from_card(_card("b")), from_card(_card("a"))])
    assert [c.auction_id for c in merged] == ["a", "b"]


def test_a_config_round_trips_through_the_seed_format() -> None:
    """`harvest backfill` and `harvest load` both read the seed file, so the registry
    has to be able to write one they still understand."""
    configs = [from_card(_card("a-1"))]
    rows = to_seed_rows(configs)
    assert [from_seed_row(row, asta_type="mantra") for row in rows] == configs


@pytest.mark.parametrize("missing", ["min_player", "max_player", "counter_time"])
def test_a_card_with_holes_still_becomes_a_config(missing: str) -> None:
    """Real cards carry nulls: an auction with no roster limit reports
    `min_player: null`, and refusing it would drop a live auction over a field
    that is legitimately absent."""
    card = _card("a-1")
    card[missing] = None
    assert from_card(card).auction_id == "a-1"
