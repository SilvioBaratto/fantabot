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
    """A player sold: who, for how much, to which team (``None`` if unnamed).

    ``bidder_user_id`` is additive — ``None`` for a ``parse_assignment`` replay, which has no
    such field, and for any caller built before this existed. It is what makes a passed lot's
    real bidder recoverable: an admin auto-skip and a raise that stood but was never confirmed
    are the same record shape (``price: 0``, no ``fantateam_id``) and differ only in whose
    ``user_id`` wrote it (`attribute_passed_lots`).
    """

    player_id: str
    price: int
    buyer_team_id: str | None
    bidder_user_id: str | None = None


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

    ``bidder_user_id`` (``record["user_id"]``) rides along on every record, sold or skipped.
    On a skip it is not "nobody" — it is whoever's action produced this record: the room admin
    auto-skipping an expired lot, or a bidder whose raise stood but the admin passed anyway,
    which the platform records identically. `attribute_passed_lots` is what tells them apart.
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
    bidder = record.get("user_id")
    return AssignmentEvent(
        player_id=player_id,
        price=price,
        buyer_team_id=buyer if isinstance(buyer, str) else None,
        bidder_user_id=bidder if isinstance(bidder, str) else None,
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


def attribute_passed_lots(
    events: Iterable[AssignmentEvent],
    *,
    admin_user_id: str | None,
    seat_by_user: Mapping[str, str],
    price_of: Mapping[str, float],
    min_bid: int = 1,
) -> tuple[list[AssignmentEvent], list[str]]:
    """A lot the room recorded as unsold, when the last raise on it was actually ours (or a
    rival's) and the admin skipped it anyway. Pure.

    Measured on the 2026-09-01 evening (`data/room_state_snapshot.jsonl`, the room's own
    ``purchases/<fl>`` ledger): 4 records read ``price: 0, fantateam_id: <absent>`` — the same
    shape ``parse_purchase`` already emits as "unsold" — while ``user_id`` names a real bidder,
    not the room admin who normally writes that shape (248 records, one admin, same evening).
    Two were ours: Sohm and Caprile, both bought at the platform's minimum and both missing
    from the bot's own ledger-derived roster all evening. The other two belonged to two
    different rivals — the same defect happening to someone else's seat, invisible to us for
    the same reason.

    **The rule is exact, not loosened.** An event whose ``bidder_user_id`` equals
    ``admin_user_id`` is left exactly as `parse_purchase` produced it — ``buyer_team_id=None``,
    a real skip — always, regardless of how many such records there are (248 on this evening).
    Confusing an admin's routine auto-skip with a stood raise would claim a lot nobody bid on.

    ``seat_by_user`` is the room's ``user_id -> fantateam_id`` map (every held seat, ours
    included — a rival's reclaimed lot must vanish from `AstaState.taken` the same way ours
    does, or the plan optimizes around a player the room has actually removed from the pool).
    A ``bidder_user_id`` absent from it (a stale or unseated id) is left unattributed rather
    than raising — the same "hold, don't end the evening" convention the rest of this package
    already keeps.

    ``price_of`` is a market-price lookup (fantacalcio id -> observed clearing price, e.g.
    ``RoomTracker``'s own ``prices``); a record with no observed price falls back to
    ``min_bid``, matching what both of the real evening's reclaimed lots actually cleared at.

    Returns the rewritten events, in the same order, and the ``player_id`` of every lot this
    reattributed — an auditable record of what changed, not a bare count.
    """
    attributed: list[AssignmentEvent] = []
    reattributed: list[str] = []
    for event in events:
        is_a_recorded_skip = event.price == 0 and event.buyer_team_id is None
        if (
            is_a_recorded_skip
            and event.bidder_user_id is not None
            and event.bidder_user_id != admin_user_id
        ):
            team = seat_by_user.get(event.bidder_user_id)
            if team is not None:
                event = replace(
                    event,
                    buyer_team_id=team,
                    price=int(price_of.get(event.player_id, min_bid)),
                )
                reattributed.append(event.player_id)
        attributed.append(event)
    return attributed, reattributed


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
