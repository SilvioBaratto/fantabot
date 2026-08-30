"""Merged auction states in, assignments out. Pure.

Both collection paths arrive here as a sequence of *merged states* — the poller
wrote them directly, and the live path reduces SSE frames into the same shape.
Neither reaches this module as frames, which is why the recorded evening
verifies this module and not the reducer.

Three behaviours carry the weight:

**Duplicates are absorbed, not counted** — but not by the ``last_update`` guard
below, which mutation testing on 2026-08-27 showed to be redundant: removing it
reconstructs the whole recorded evening to the same 11,498 assignments and
70,627 rungs. (That figure read 70,152 until 2026-08-30, when re-running the
comparison found both paths at 70,627 — a later fix moved the count and the prose
did not follow it. The claim was and is that the two agree.) Two other rules do the work, and they are the ones to preserve:
``sold`` keeps the first close per (auction, player), and a rung is appended only
on a *price change*. The guard stays as cheap insurance against an input shape
neither of those covers, and is described here as what it is rather than as what
it was assumed to be.

**A ladder belongs to one player's turn.** The node is a single mutable slot:
when the player on the block changes, the previous ladder is finished. Failing
to reset there would splice unrelated auctions of different players into one
implausible run.

**The last close for a player wins.** After a close the node keeps returning the
closed state until the next call begins, so a naive pass counts one sale many
times — and the first draft drew the wrong rule from that true observation.
An annulled call is re-auctioned, and the *second* close is the real sale. In
the recorded evening 271 (auction, player) pairs close at two different prices;
first-wins recorded the superseded one, lost the buyer on 175 of them, and
undercounted the evening's spend by 1,814 credits.

Last-wins covers both: a re-emission carries the same price, so first and last
agree, and a genuine re-auction takes the later close — which is also the turn
whose ladder ``first_call`` has already reset to, so price and ladder now
describe the same turn.

``scripts/resolve_aste_live.py`` still takes the first, so the two disagree on
those 271 by design. The recorded evening remains a regression test for the
*count*, which is identical under either rule; it is not one for the prices, and
a test now says so rather than implying otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fantabot.domain.harvest.models import Assignment, Bid

CLOSE = "close_auction"
FIRST_CALL = "first_call"

#: Distinguishes "no player on the block" from "this auction is new to us".
_UNSEEN = object()

#: States that put a price on the board. ``confirm`` and ``reset`` do not: the
#: first clears the slot after a sale, the second annuls a call outright.
BIDDING = frozenset({FIRST_CALL, "raise", CLOSE})


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
    #: (auction, player) -> its index in ``assignments``, so a later close can
    #: replace an earlier one in place rather than appending a second sale.
    sold: dict[tuple[str, str], int] = {}
    ladders: dict[str, list[Bid]] = {}
    on_the_block: dict[str, object] = {}
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
        # `None` is a real value here — a `confirm` state carries no player_id
        # because the slot is empty between sales. Using it as "not seen yet"
        # meant an auction whose first observed state had no player never got a
        # ladder at all, and the next raise raised KeyError. Found by a live
        # capture that began mid-turn, not by the recorded evening, which only
        # ever started on a first_call.
        update_type = state.get("update_type")

        # A turn begins on `first_call`, and a turn can begin twice for the same
        # player: an annulled call puts him back on the block from zero. Keying
        # the reset on the *player* changing glued those two turns together —
        # observed in auction `ccdbe75d` on 2026-08-26, where bidding climbed to
        # 17, the call was annulled, and the player then sold at 0. The ladder
        # came out climbing to 17 and falling to 0, which an ascending auction
        # cannot produce, and an opponent model fitted on it would see a bidding
        # war that ended at zero.
        previous = on_the_block.get(auction_id, _UNSEEN)
        if update_type == FIRST_CALL or previous is _UNSEEN or previous != player_id:
            on_the_block[auction_id] = player_id
            ladders[auction_id] = []

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
        # Last close wins: a re-emission repeats the same price, and a re-auction
        # supersedes the annulled one. Recorded in order of first sale so the
        # output still reads chronologically.
        key = (auction_id, player_id)
        assignment = Assignment(
            auction_id=auction_id,
            player_id=player_id,
            price=rung.price,
            buyer_team_id=rung.team_id,
            closed_at_ms=state.get("last_update"),
            ladder=tuple(ladder),
        )
        if key in sold:
            assignments[sold[key]] = assignment
        else:
            sold[key] = len(assignments)
            assignments.append(assignment)

    return assignments
