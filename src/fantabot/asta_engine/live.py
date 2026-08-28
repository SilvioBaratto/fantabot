"""Live room read: turn raw FantaLab room states into normalized assignment events.

A player is SOLD when the room emits a ``close_auction`` state — the only state the rolling
optimizer reacts to. ``parse_assignment`` is pure (one raw state in, an event or ``None``
out) and ``normalize`` maps a whole sequence, so the reaction logic is tested without a
socket.

The async subscription to a real room is a **thin, deferred shell** over ``aste.stream``:
the own-room feed path (the public spectator Firebase node vs the authenticated API) is
still an open question, so it is wired last. Everything the optimizer needs downstream is
driven off ``AssignmentEvent``, which a replayed capture produces exactly as a live room
would.
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
