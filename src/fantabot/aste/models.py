"""Value types for auction reconstruction. Pure: no I/O, no SQLAlchemy.

Frozen, like fantabot's other value types, so a caller cannot quietly mutate a
reconstructed ladder and have the change survive into storage.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bid:
    """One rung of a ladder: what was offered, by whom, when.

    ``team_id`` is nullable because the opening call has no bidder yet — the
    player is on the block at a price nobody has claimed.
    """

    price: int
    team_id: str | None
    at_ms: int | None


@dataclass(frozen=True, slots=True)
class Assignment:
    """A player sold, and the bidding that got there.

    The clearing price alone is what polling already produced. ``ladder`` is the
    part that needed subscriptions to observe, and it is what makes an opponent
    model fittable rather than invented — every rung names the team that pushed.
    """

    auction_id: str
    player_id: str
    price: int
    buyer_team_id: str | None
    closed_at_ms: int | None
    ladder: tuple[Bid, ...]
