"""Row building for the backfill, checked without a database.

The input is 144,518 states. A mistake in the shape of a row is otherwise
discovered at row 140,000, which is why every row is built by a pure function
and asserted here rather than at the far end of a load.
"""

from __future__ import annotations

import json
from pathlib import Path

from fantabot.aste.backfill import assignment_rows, auction_rows, build, event_rows
from fantabot.aste.reconstruct import reconstruct

FIXTURE = Path(__file__).parent / "fixtures" / "states" / "one_auction.jsonl"
STATES = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
AUCTION_ID = STATES[0]["auction_id"]

SEED = [[AUCTION_ID, "15", 10, 500, 25, 25, "random", "free", 7, 7, "FIXTURE LEAGUE"]]


def test_a_seed_entry_becomes_a_registry_row() -> None:
    (row,) = auction_rows(SEED, "mantra")
    assert row["id"] == AUCTION_ID
    assert row["db_shard"] == "15"
    assert row["asta_type"] == "mantra"
    assert (row["num_teams"], row["num_credits"]) == (10, 500)


def test_the_format_comes_from_the_caller_not_the_seed() -> None:
    """The poller-era seed never stored `asta_type` — it only ever collected one
    format. A backfill must not pretend the old file knew something it did not."""
    assert auction_rows(SEED, "classic")[0]["asta_type"] == "classic"


def test_events_for_unknown_auctions_are_dropped_not_raised() -> None:
    """A foreign key would refuse them anyway. Dropping here gives the caller a
    count instead of an exception partway through a 144,518-row load."""
    assert event_rows(STATES, known=set()) == []
    assert len(event_rows(STATES, known={AUCTION_ID})) == len(STATES)


def test_an_event_row_keeps_the_state_whole() -> None:
    row = event_rows(STATES, known={AUCTION_ID})[0]
    assert row["payload"] == STATES[0]["state"], "the raw state must survive verbatim"
    assert row["last_update"] == STATES[0]["state"].get("last_update")


def test_unmatched_players_are_counted_not_dropped() -> None:
    """2 of 407 on 2026-08-26, both signings newer than our last scrape. The count
    is a staleness signal; losing the row would lose a real price."""
    rows, unmatched = assignment_rows(reconstruct(STATES), listone={})
    assert unmatched == len(rows), "an empty listone matches nobody"
    assert all(row["fantacalcio_id"] is None for row in rows)
    assert rows, "the assignments themselves must still be built"


def test_a_known_player_is_bridged_by_id_not_by_name() -> None:
    assignments = reconstruct(STATES)
    listone = {assignments[0].player_id: {"fantacalcio_id": 4242, "name": "irrelevant"}}
    rows, unmatched = assignment_rows(assignments, listone)
    assert rows[0]["fantacalcio_id"] == 4242
    assert unmatched == len(rows) - 1


def test_an_assignment_row_carries_its_ladder_as_plain_data() -> None:
    rows, _ = assignment_rows(reconstruct(STATES), listone={})
    ladders = [row["ladder"] for row in rows if row["ladder"]]
    assert ladders, "no ladder survived into the row"
    assert all(set(rung) == {"price", "team_id", "at_ms"} for rung in ladders[0])


def test_build_produces_a_consistent_set() -> None:
    auctions, events, assignments, unmatched = build(STATES, SEED, {}, "mantra")
    assert len(auctions) == 1
    assert len(events) == len(STATES)
    assert len(assignments) == len(reconstruct(STATES))
    assert unmatched == len(assignments)
    assert {a["asta_id"] for a in assignments} <= {row["id"] for row in auctions}
