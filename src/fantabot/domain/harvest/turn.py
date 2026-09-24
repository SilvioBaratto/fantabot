"""The rules both folds share, stated once. Pure -- no I/O, no clock, no state.

``reconstruct`` reads a whole evening in one pass; ``incremental`` carries the same fold
across windows, so ``harvest load --follow`` need not re-read a 1.22 GB landing zone on
every tick. They are two foldings of one set of rules and they differ in exactly two
things, both deliberate and both documented where they live:

* ``reconstruct`` scopes its ``last_update`` guard to the **evening** and applies it
  **before** the turn reset; ``incremental`` scopes it to the **turn** and resets first.
* ``reconstruct`` collapses repeated closes through its ``sold`` map before returning;
  ``incremental`` emits every close it sees and the caller drains them.

Everything else was written twice, comment for comment, with ``incremental`` reaching
across the module boundary for a private ``_bid`` to do it. Those rules are here:

* which records are records at all -- ``state_of``
* when a player's turn begins, and the ladder with it -- ``starts_new_turn``
* what a state says about the price on the board -- ``bid_of`` -- and when that is a new
  rung rather than the same offer observed twice -- ``append_rung``
* what closes a sale, and the row it writes -- ``closed_sale``

Each carries the incident that fixed it, so the two folds cannot drift apart on any of
them and a reader is not left to diff two loops to find out whether they agree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fantabot.domain.harvest.models import Assignment, Bid
from fantabot.domain.shared.update_types import CLOSE, FIRST_CALL, RAISE

#: States that put a price on the board. ``confirm`` and ``reset`` do not: the
#: first clears the slot after a sale, the second annuls a call outright.
#:
#: The tokens themselves live in ``domain/shared/update_types``: ``domain/asta/live`` reads the
#: same ``auction/`` node and declared ``close_auction`` a second time. This set is not a
#: token, it is which of them this fold treats as a price, so it stays with the fold.
BIDDING = frozenset({FIRST_CALL, RAISE, CLOSE})


def state_of(row: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    """``(auction_id, state)`` for a collector record, or ``None`` if it is not one.

    Collector records are ``{"auction_id": ..., "state": {...}}``. A line that is not
    that shape is skipped rather than fatal: the landing zone is appended to by a live
    socket, and an evening must not end on one malformed record.
    """
    auction_id = row.get("auction_id")
    state = row.get("state")
    if not isinstance(auction_id, str) or not isinstance(state, Mapping):
        return None
    return auction_id, state


def starts_new_turn(
    update_type: Any, player_id: Any, previous: Any, *, seen_before: bool
) -> bool:
    """Does this state begin a new turn -- and so finish the ladder that was running?

    A turn begins on ``first_call``, and a turn can begin **twice for the same player**:
    an annulled call puts him back on the block from zero. Keying the reset on the player
    changing alone glued those two turns together -- observed in auction ``ccdbe75d`` on
    2026-08-26, where bidding climbed to 17, the call was annulled, and the player then
    sold at 0. The ladder came out climbing to 17 and falling to 0, which an ascending
    auction cannot produce, and an opponent model fitted on it would see a bidding war
    that ended at zero.

    ``seen_before`` is passed rather than inferred from ``previous``, because ``None`` is
    a real value for the player on the block -- a ``confirm`` state carries no
    ``player_id``, the slot being empty between sales. Reading "no player" as "auction
    not seen yet" left an auction whose first observed state had no player without a
    ladder at all, and the next raise raised ``KeyError``; found by a live capture that
    began mid-turn, not by the recorded evening, which only ever starts on a first call.
    """
    return update_type == FIRST_CALL or not seen_before or previous != player_id


def bid_of(state: Mapping[str, Any]) -> Bid | None:
    """The rung this state puts on the board, or ``None`` when it names no price."""
    price = state.get("price")
    if not isinstance(price, int):
        return None
    return Bid(price=price, team_id=state.get("fantateam_id"), at_ms=state.get("last_bid_time"))


def append_rung(ladder: list[Bid], rung: Bid | None) -> None:
    """Extend ``ladder`` in place, but only where the price actually moved.

    The node keeps returning the state it holds, so the same offer is observed many
    times; a re-observation at the same price is not a rung. This is one of the two
    rules that absorb duplicates -- the other is that the first close per
    ``(auction, player)`` keeps its place in the output -- and between them the
    ``last_update`` guard is nearly redundant. Nearly: see ``incremental``'s docstring
    for the two ladders in 167,894 where it is not.
    """
    if rung is not None and (not ladder or ladder[-1].price != rung.price):
        ladder.append(rung)


def closed_sale(
    auction_id: str,
    player_id: Any,
    update_type: Any,
    rung: Bid | None,
    ladder: Sequence[Bid],
    closed_at_ms: Any,
) -> Assignment | None:
    """The sale this state closes, or ``None`` when it closes none.

    Three things must hold together: the state says ``close_auction``, it names a player,
    and it carries a price. A close with no price is not a sale we can record -- the
    ladder is what makes it one -- and ``ladder`` is snapshotted here, because the caller
    goes on mutating the list it passed.
    """
    if update_type != CLOSE or not isinstance(player_id, str) or rung is None:
        return None
    return Assignment(
        auction_id=auction_id,
        player_id=player_id,
        price=rung.price,
        buyer_team_id=rung.team_id,
        closed_at_ms=closed_at_ms,
        ladder=tuple(ladder),
    )
