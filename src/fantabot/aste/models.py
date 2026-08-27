"""Value types for auction reconstruction. Pure: no I/O, no SQLAlchemy.

Frozen, like fantabot's other value types, so a caller cannot quietly mutate a
reconstructed ladder and have the change survive into storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Firebase namespaces are `fantalab-0` … `fantalab-19`. Nineteen were counted
#: on 2026-08-27; the bound is loose enough for more and tight enough that
#: nothing but a number gets through.
_SHARD = re.compile(r"^[0-9]{1,3}$")


class ShardError(ValueError):
    """A shard that must not be interpolated into a URL."""


def valid_shard(value: Any) -> str:
    """The shard as a string, or refuse it.

    ``db`` is read off an auction card — observed content, not ours — and lands
    in a hostname. A ``#`` truncates the rest of the template, so ``evil.com#``
    produced the host ``fantalab-evil.com`` and the collector would have
    connected to it. Checked where the value enters *and* where the URL is
    built, because one path forgetting is exactly how this class of bug returns.
    """
    text = str(value)
    if not _SHARD.match(text):
        raise ShardError(f"{value!r} is not a Firebase shard; expected 0-999")
    return text


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
