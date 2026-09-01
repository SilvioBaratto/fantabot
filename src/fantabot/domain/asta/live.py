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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import parse_qs, urlsplit

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


def resolve_ids(
    events: Iterable[AssignmentEvent], bridge: Mapping[str, int]
) -> tuple[list[AssignmentEvent], list[str]]:
    """Re-key events from FantaLab UUIDs to fantacalcio ids. Pure.

    **This is the boundary the engine was missing.** Everything downstream —
    `AstaState.owned`, the pool, the value model, the legality matrix — is keyed by
    fantacalcio id. Events arrive keyed by FantaLab UUID. Without this, the first lot
    we win puts a UUID into `owned` and `optimize_roster` raises `InfeasibleRoster`
    for an id that is not in the pool, which is what `asta-bid` did.

    Returns the resolved events and the UUIDs that could not be mapped. Unresolvable
    events are **dropped, and counted** — the same rule the loader applies to events
    for auctions it has never heard of. A player the listone does not know cannot be
    valued, so keeping him would put the same unmappable id into `owned` by a longer
    route; and a drop nobody counts reads as an empty input, which this package has
    had to learn more than once.
    """
    resolved: list[AssignmentEvent] = []
    unknown: list[str] = []
    for event in events:
        fid = bridge.get(event.player_id)
        if fid is None:
            unknown.append(event.player_id)
            continue
        resolved.append(replace(event, player_id=str(fid)))
    return resolved, unknown


class InvitationLink(ValueError):
    """A `/join-asta?invitation_id=` link: the right room, one authenticated call away.

    Its own type rather than a `ValueError` among others, because the caller has to tell
    "wrong link" from "right link, resolvable" and say something different about each. This
    one is what an admin actually sends, so refusing it flatly would hand the operator a dead
    end holding the only link they had.
    """

    def __init__(self, invitation_id: str) -> None:
        self.invitation_id = invitation_id
        super().__init__(
            f"that is an invitation link ({invitation_id}), not a room link. Its uuid is an "
            "invitation, not a fantaleague id, and only POST /fantaleague/fetchByInvitation "
            "with a Bearer can turn one into the other (docs/fantalab/06 §3). Open it in the "
            "browser and paste the /asta?asta=... link the room shows."
        )


#: A uuid v4 as FantaLab writes them, and nothing else. Matching loosely here would let a
#: typo through as an id and turn a paste error into a subscription that waits for ever.
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def parse_room_url(text: str) -> str:
    """A pasted room link, or a bare uuid, as a fantaleague id. Pure — no network.

    The `asta` query parameter **is** the fantaleague id rather than a separate handle
    (`docs/fantalab/03-platform-map.md`), which is why this needs nothing but the string.

    Raises `InvitationLink` for a `/join-asta` link — a different uuid entirely — and a plain
    `ValueError` for anything else, naming what to paste instead.
    """
    candidate = text.strip()
    if not candidate:
        raise ValueError("nothing to parse: paste the room link or its fantaleague id")

    if _UUID.match(candidate):
        return candidate

    parsed = urlsplit(candidate)
    query = parse_qs(parsed.query)

    invitation = query.get("invitation_id", [None])[0]
    if invitation or "join-asta" in parsed.path:
        raise InvitationLink(invitation or "unknown")

    asta = query.get("asta", [None])[0]
    if asta and _UUID.match(asta):
        return asta

    raise ValueError(
        f"{candidate!r} is not a FantaLab room link. Paste the address of the room itself "
        "(app.fantalab.it/asta?asta=...) or its fantaleague id."
    )


#: FantaLab's own timers, used when a room does not declare its own
#: (`docs/fantalab/01-auction-engine.md:22`).
DEFAULT_COUNTER_TIME = 10
DEFAULT_COUNTER_TIME_FIRST = 20


def seconds_left(
    snapshot: Mapping[str, Any] | None,
    *,
    now_ms: int,
    counter_time: int | None,
    counter_time_first: int | None,
) -> float | None:
    """How long the lot on the block has, or ``None`` when the snapshot cannot say. Pure.

    ``remaining = (is_first ? counter_time_first : counter_time) - (now - last_bid_time)``,
    from ``docs/fantalab/01-auction-engine.md:263``. The first call gets the longer clock
    because a called player has to be noticed before anyone can bid on him.

    ``now_ms`` is a parameter for the same reason ``sentiment.as_of`` is: a pure module that
    reads a clock has tests that are a coin flip.

    ``None`` and ``0.0`` say different things and are kept apart — "we cannot tell" renders as
    ``--`` while "expired" renders as expired, and the LOT pane turns red under two seconds,
    which every negative number would satisfy for ever.
    """
    if not snapshot:
        return None
    last = snapshot.get("last_bid_time")
    if not isinstance(last, int) or isinstance(last, bool):
        return None

    price = snapshot.get("price")
    is_first = not (isinstance(price, int) and not isinstance(price, bool) and price > 0)
    window = counter_time_first if is_first else counter_time
    if window is None:
        window = DEFAULT_COUNTER_TIME_FIRST if is_first else DEFAULT_COUNTER_TIME

    return max(0.0, window - (now_ms - last) / 1000.0)
