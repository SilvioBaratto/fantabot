"""Live room read: turn raw FantaLab room states into normalized assignment events.

Two ways in, both producing the same ``AssignmentEvent`` the rolling optimizer reacts to:

* **The ledger** (``parse_purchase`` / ``purchases_to_events``) — the authoritative signal for
  a **live** room. A sale is a record on the ``purchases/<fl>`` node. This is what the live feed
  keys off, because it is verified robust where ``close_auction`` is not
  (``docs/fantalab/06-asta-write-path.md`` §10): a ``close_auction`` freezes a lot but is
  **reversible** (RIAPRI), and **ASSEGNA** lots settle on a separate node sometimes without any
  ``close_auction`` at all. A purchase is written once, on confirm — so keying off it can neither
  double-count a reopened lot nor miss an assigned one.

* **A close state** (``parse_assignment`` / ``normalize``) — kept for **replays** of a captured
  ``auction/`` snapshot stream, where each snapshot at close-time carries the last raise's price
  and buyer. Not used for the live path.

All four are pure — one record/state in, an event or ``None`` out — so the reaction logic is
tested without a socket. The async subscription to a real room is a thin shell over
``fantalab`` transport; everything downstream is driven off ``AssignmentEvent``, which a replay
and a live ledger produce identically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: The room state that closes a player's auction — i.e. a sale.
CLOSE = "close_auction"


@dataclass(frozen=True)
class AssignmentEvent:
    """A player sold: who, for how much, to which team (``None`` if unnamed)."""

    player_id: str
    price: int
    buyer_team_id: str | None


def parse_assignment(state: Mapping[str, Any]) -> AssignmentEvent | None:
    """One raw room state -> an assignment event, or ``None`` if it is not a sale.

    A garbled record (not a mapping) is ignored, not fatal: a live evening must not die on
    one malformed line.
    """
    if not isinstance(state, Mapping) or state.get("update_type") != CLOSE:
        return None
    player_id = state.get("player_id")
    price = state.get("price")
    if not isinstance(player_id, str) or not isinstance(price, int):
        return None
    buyer = state.get("fantateam_id")
    return AssignmentEvent(
        player_id=player_id,
        price=price,
        buyer_team_id=buyer if isinstance(buyer, str) else None,
    )


def normalize(states: Iterable[Mapping[str, Any]]) -> list[AssignmentEvent]:
    """Every sale in a sequence of raw states, in order. Pure — replay-friendly."""
    events = (parse_assignment(state) for state in states)
    return [event for event in events if event is not None]


def parse_purchase(record: Mapping[str, Any]) -> AssignmentEvent | None:
    """One ``purchases/<fl>/<id>`` record → an assignment event, or ``None`` if it is garbled.

    The ledger is the authoritative sale signal for a live room. An **unsold / skipped** lot is
    a real record with ``price: 0`` and no ``fantateam_id`` — it is emitted with
    ``buyer_team_id=None``, **not dropped**, so the pool sees the player leave the board and the
    opponent tracker correctly attributes it to nobody.
    """
    if not isinstance(record, Mapping):
        return None
    player_id = record.get("player_id")
    price = record.get("price")
    if not isinstance(player_id, str):
        return None
    if isinstance(price, bool) or not isinstance(price, int):  # a JSON bool is not a price
        return None
    buyer = record.get("fantateam_id")
    return AssignmentEvent(
        player_id=player_id,
        price=price,
        buyer_team_id=buyer if isinstance(buyer, str) else None,
    )


def purchases_to_events(purchases: Mapping[str, Mapping[str, Any]]) -> list[AssignmentEvent]:
    """The whole ``purchases/<fl>`` ledger → assignment events, ordered by ``created_at``. Pure.

    Firebase delivers the collection as an unordered map keyed by ``purchase_id``; the write
    order is recovered from each record's ``created_at``. A reopened lot never wrote a purchase,
    so it cannot appear here — no phantom sale.
    """
    records = [record for record in purchases.values() if isinstance(record, Mapping)]
    records.sort(key=lambda record: record.get("created_at") or 0)
    events = (parse_purchase(record) for record in records)
    return [event for event in events if event is not None]
