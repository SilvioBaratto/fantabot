"""Turning a recorded collector log into rows the database can hold.

The row-building here is pure and the writing is a thin shell around it, so the
shape of every row is testable without a database — which matters because the
input is 144,518 states and a mistake in the shape is discovered at row 140,000
otherwise.

**This is the same path the live loader will use.** The backfill is not a
one-off importer: if it grows its own way of building rows, one of the two ends
up untested and the difference surfaces on an evening that cannot be recollected.

Two joins happen here, and both are exact rather than fuzzy. An auction's
configuration comes from the seed the scan wrote; a player's ``fantacalcio_id``
comes from ``GET /v2/listone``, which supplies it directly. Nothing is matched
on a name.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fantabot.aste.models import Assignment
from fantabot.aste.reconstruct import reconstruct

#: Positions in a seed row, which is a list rather than an object because the
#: scan writes it straight out of the page's props.
SEED_ID, SEED_DB, SEED_TEAMS, SEED_CREDITS, SEED_MIN, SEED_MAX = 0, 1, 2, 3, 4, 5
SEED_MODE, SEED_RAISE, SEED_TIMER, SEED_TIMER_FIRST = 6, 7, 8, 9


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """What a run wrote, per table."""

    auctions: int
    events: int
    assignments: int
    unlinked_players: int


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def auction_rows(seed: Iterable[Sequence[Any]], asta_type: str) -> list[dict[str, Any]]:
    """Registry rows from a scan seed.

    ``asta_type`` is passed in rather than read from the seed: the poller-era
    seed predates storing it, because it only ever collected one format. That is
    the limitation this phase exists to remove, and a backfill should not pretend
    the old file knew something it did not.
    """
    rows = []
    for entry in seed:
        rows.append(
            {
                "id": entry[SEED_ID],
                "db_shard": str(entry[SEED_DB]),
                "asta_type": asta_type,
                "name": entry[-1] if len(entry) > SEED_TIMER_FIRST else None,
                "num_teams": entry[SEED_TEAMS],
                "num_credits": entry[SEED_CREDITS],
                "min_player": entry[SEED_MIN],
                "max_player": entry[SEED_MAX],
                "asta_mode": entry[SEED_MODE],
                "raise_mode": entry[SEED_RAISE],
                "counter_time": entry[SEED_TIMER],
                "counter_time_first": entry[SEED_TIMER_FIRST],
            }
        )
    return rows


def event_rows(states: Iterable[Mapping[str, Any]], known: set[str]) -> list[dict[str, Any]]:
    """Frame rows, dropping any auction the registry does not know.

    A foreign key would refuse those anyway; dropping them here means the caller
    gets a count rather than an exception halfway through a 144,518-row load.
    """
    rows = []
    for row in states:
        auction_id = row.get("auction_id")
        state = row.get("state")
        if not isinstance(auction_id, str) or auction_id not in known:
            continue
        if not isinstance(state, Mapping):
            continue
        rows.append(
            {
                "asta_id": auction_id,
                "last_update": state.get("last_update"),
                "seen_at": datetime.fromisoformat(str(row["seen_at"])),
                "update_type": state.get("update_type"),
                "payload": dict(state),
            }
        )
    return rows


def assignment_rows(
    assignments: Iterable[Assignment],
    listone: Mapping[str, Mapping[str, Any]],
    known_players: frozenset[int] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Assignment rows, plus how many could not be linked to a player.

    **Two different gaps, one outcome.** The listone may not know a player at
    all, or — the case that actually bites — it knows him and our ``players``
    table does not. On 2026-08-27 the second sank a full backfill:

        ForeignKeyViolation: Key (fantacalcio_id)=(7581) is not present in
        table "players"

    7581 is Konaté A., a signing newer than our last scrape. The foreign key was
    right to refuse it; nulling the link is right too, because an auction price
    is unrepeatable and a stale reference table is not a reason to lose one.

    ``known_players`` is the set of ids ``players`` actually holds. Pass it and
    unknown links are dropped to NULL; omit it and the caller is trusting the
    reference table to be current, which on this data it was not.

    The count comes back rather than going to a log because it is a staleness
    signal: 2 of 407 is a transfer window, 200 of 407 is a broken join.
    """
    rows, unlinked = [], 0
    for a in assignments:
        entry = listone.get(a.player_id)
        fantacalcio_id = entry.get("fantacalcio_id") if entry else None
        if known_players is not None and fantacalcio_id not in known_players:
            fantacalcio_id = None
        if fantacalcio_id is None:
            unlinked += 1
        rows.append(
            {
                "asta_id": a.auction_id,
                "player_uuid": a.player_id,
                "fantacalcio_id": fantacalcio_id,
                "price": a.price,
                "buyer_team_id": a.buyer_team_id,
                "closed_at_ms": a.closed_at_ms,
                "ladder": [
                    {"price": b.price, "team_id": b.team_id, "at_ms": b.at_ms} for b in a.ladder
                ],
            }
        )
    return rows, unlinked


def build(
    states: Sequence[Mapping[str, Any]],
    seed: Sequence[Sequence[Any]],
    listone: Mapping[str, Mapping[str, Any]],
    asta_type: str,
    known_players: frozenset[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    """Every row a backfill writes, built without touching a database."""
    auctions = auction_rows(seed, asta_type)
    known = {row["id"] for row in auctions}
    events = event_rows(states, known)
    assignments, unlinked = assignment_rows(reconstruct(states), listone, known_players)
    assignments = [row for row in assignments if row["asta_id"] in known]
    return auctions, events, assignments, unlinked
