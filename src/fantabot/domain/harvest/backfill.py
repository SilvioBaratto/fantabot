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

from fantabot.domain.harvest.models import Assignment
from fantabot.domain.harvest.reconstruct import reconstruct
from fantabot.domain.harvest.registry import from_seed_row


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """What a run wrote, per table."""

    auctions: int
    events: int
    assignments: int
    unlinked_players: int


@dataclass(frozen=True, slots=True)
class DroppedEvents:
    """Why records did not become event rows, one count per reason.

    Separate counts because the reasons mean different things. An unknown
    auction is routine — the collector followed rooms the seed does not
    describe. A malformed state or an unusable timestamp is a broken record,
    and a run that quietly drops those looks exactly like a run with nothing
    to load.
    """

    unknown_auction: int = 0
    malformed_state: int = 0
    bad_timestamp: int = 0

    @property
    def total(self) -> int:
        return self.unknown_auction + self.malformed_state + self.bad_timestamp

    @property
    def any(self) -> bool:
        return self.total > 0

    def summary(self) -> str:
        return (
            f"unknown auction {self.unknown_auction} · "
            f"malformed state {self.malformed_state} · "
            f"bad timestamp {self.bad_timestamp}"
        )


@dataclass(frozen=True, slots=True)
class BuiltRows:
    """Everything a backfill would write, plus what it refused to.

    Named fields rather than a tuple: this grew from four elements to five, and
    a positional unpack at two call sites is one inserted field away from
    loading assignments into the events table.
    """

    auctions: list[dict[str, Any]]
    events: list[dict[str, Any]]
    assignments: list[dict[str, Any]]
    unlinked_players: int
    dropped_events: DroppedEvents


def _parse_seen_at(value: Any) -> datetime | None:
    """The record's own timestamp, or ``None`` if it does not have a usable one.

    ``str(value)`` was applied before parsing, which turned a *missing* key into
    the string ``"None"`` and a ``KeyError`` into a ``ValueError`` — the same
    crash, further from its cause.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def auction_rows(seed: Iterable[Sequence[Any]], asta_type: str) -> list[dict[str, Any]]:
    """Registry rows from a scan seed.

    ``asta_type`` is passed in rather than read from the seed: the poller-era
    seed predates storing it, because it only ever collected one format. That is
    the limitation this phase exists to remove, and a backfill should not pretend
    the old file knew something it did not.

    Parsing belongs to ``registry``, which owns the format. A first draft read
    the row here with its own index constants — two independent readings of one
    positional file, which drift silently the moment a field is added.
    """
    return [
        {
            "id": config.auction_id,
            "db_shard": config.db_shard,
            "asta_type": config.asta_type,
            "name": config.name,
            "num_teams": config.num_teams,
            "num_credits": config.num_credits,
            "min_player": config.min_player,
            "max_player": config.max_player,
            "asta_mode": config.asta_mode,
            "raise_mode": config.raise_mode,
            "counter_time": config.counter_time,
            "counter_time_first": config.counter_time_first,
        }
        for config in (from_seed_row(entry, asta_type=asta_type) for entry in seed)
    ]


def event_rows(
    states: Iterable[Mapping[str, Any]], known: set[str]
) -> tuple[list[dict[str, Any]], DroppedEvents]:
    """Frame rows, plus a count of every record that did not become one.

    A foreign key would refuse an unknown auction anyway; dropping it here means
    the caller gets a count rather than an exception halfway through a
    144,518-row load. The count is now returned instead of merely promised —
    a silent drop is indistinguishable from an empty input, and one of these
    reasons is a broken record rather than a routine one.
    """
    rows = []
    unknown = malformed = undated = 0
    for row in states:
        auction_id = row.get("auction_id")
        state = row.get("state")
        if not isinstance(auction_id, str) or auction_id not in known:
            unknown += 1
            continue
        if not isinstance(state, Mapping):
            malformed += 1
            continue
        seen_at = _parse_seen_at(row.get("seen_at"))
        if seen_at is None:
            undated += 1
            continue
        rows.append(
            {
                "asta_id": auction_id,
                "last_update": state.get("last_update"),
                "seen_at": seen_at,
                "update_type": state.get("update_type"),
                "payload": dict(state),
            }
        )
    return rows, DroppedEvents(unknown, malformed, undated)


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
) -> BuiltRows:
    """Every row a backfill writes, built without touching a database."""
    auctions = auction_rows(seed, asta_type)
    known = {row["id"] for row in auctions}
    events, dropped = event_rows(states, known)
    assignments, unlinked = assignment_rows(reconstruct(states), listone, known_players)
    assignments = [row for row in assignments if row["asta_id"] in known]
    return BuiltRows(auctions, events, assignments, unlinked, dropped)
