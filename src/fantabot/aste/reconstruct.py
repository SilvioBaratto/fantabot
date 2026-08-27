"""Merged auction states in, assignments out. Pure.

Both collection paths arrive here as a sequence of *merged states* — the poller
wrote them directly, and the live path reduces SSE frames into the same shape.
Neither reaches this module as frames, which is why the recorded evening
verifies this module and not the reducer.

Three behaviours carry the weight:

**Duplicates are absorbed, not counted.** A restarted collector re-emits the
current state of every auction it is watching; 2026-08-26 saw eleven restarts.
Those repeats carry the same ``last_update``, so that is the identity used.

**A ladder belongs to one player's turn.** The node is a single mutable slot:
when the player on the block changes, the previous ladder is finished. Failing
to reset there would splice unrelated auctions of different players into one
implausible run.

**The first close for a player wins.** After a close the node keeps returning
the closed state until the next call begins, so a naive pass counts one sale
many times. ``scripts/resolve_aste_live.py`` made the same choice, and matching
it is what lets the recorded evening be a regression test rather than a
re-derivation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fantabot.aste.models import Assignment, Bid

CLOSE = "close_auction"

#: States that put a price on the board. ``confirm`` and ``reset`` do not: the
#: first clears the slot after a sale, the second annuls a call outright.
BIDDING = frozenset({"first_call", "raise", CLOSE})


def _bid(state: Mapping[str, Any]) -> Bid | None:
    price = state.get("price")
    if not isinstance(price, int):
        return None
    return Bid(price=price, team_id=state.get("fantateam_id"), at_ms=state.get("last_bid_time"))


def reconstruct(rows: Iterable[Mapping[str, Any]]) -> list[Assignment]:
    """Every sale in ``rows``, in the order the sales closed.

    ``rows`` are collector records — ``{"auction_id": ..., "state": {...}}`` —
    consumed in the order given, which is the order they were observed.
    """
    seen_updates: set[tuple[str, Any]] = set()
    sold: set[tuple[str, str]] = set()
    ladders: dict[str, list[Bid]] = {}
    on_the_block: dict[str, str | None] = {}
    assignments: list[Assignment] = []

    for row in rows:
        auction_id = row.get("auction_id")
        state = row.get("state")
        if not isinstance(auction_id, str) or not isinstance(state, Mapping):
            continue

        # A restart re-sends what the node already held. Same last_update, same
        # state: nothing happened, so nothing is recorded.
        stamp = state.get("last_update")
        if stamp is not None:
            if (auction_id, stamp) in seen_updates:
                continue
            seen_updates.add((auction_id, stamp))

        player_id = state.get("player_id")
        if on_the_block.get(auction_id) != player_id:
            on_the_block[auction_id] = player_id
            ladders[auction_id] = []

        update_type = state.get("update_type")
        if update_type not in BIDDING:
            continue

        rung = _bid(state)
        ladder = ladders[auction_id]
        # Only a change in price is a rung; a re-observation at the same price is
        # the same offer seen twice.
        if rung is not None and (not ladder or ladder[-1].price != rung.price):
            ladder.append(rung)

        if update_type != CLOSE or not isinstance(player_id, str) or rung is None:
            continue
        if (auction_id, player_id) in sold:
            continue
        sold.add((auction_id, player_id))
        assignments.append(
            Assignment(
                auction_id=auction_id,
                player_id=player_id,
                price=rung.price,
                buyer_team_id=rung.team_id,
                closed_at_ms=state.get("last_update"),
                ladder=tuple(ladder),
            )
        )

    return assignments
