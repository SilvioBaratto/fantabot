"""Pure bid decision: the live lot in, the raise to place (or ``None``) out.

Pure — no socket, no HTTP, no clock of its own. The transport
(``fantalab.rtdb``, a later task) sends verbatim whatever this returns; a boundary test proves
this module imports nothing that could reach the network or the database, so the whole decision
is testable with fakes.

The guards are two kinds, and both matter:

* **Server-mirrored** (``docs/fantalab/06-asta-write-path.md`` §6, §10): a bid must beat the
  current price and name the lot on the block, or the RTDB rules reject it ``401``. We reimplement
  them so a doomed write is never sent — a wasted round trip, and a lost race made
  indistinguishable from a real refusal.
* **Ours**: never bid above the walk-away, never over the remaining budget, never against our own
  standing bid, and honour the 500 ms floor the room enforces client-side.

The bid is the *minimum* raise (``current + step``, ``step = 1`` in free mode) — the walk-away is
a ceiling we refuse to cross, not a price we volunteer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Firebase server-timestamp sentinel — the room stamps the real time on write.
SERVER_TIMESTAMP: dict[str, str] = {".sv": "timestamp"}
#: The client-side debounce the room enforces; a bid inside it is refused locally.
FLOOR_MS = 500


@dataclass(frozen=True)
class Seat:
    """Our identity in the room — both ids ride on every bid."""

    fantateam_id: str
    user_id: str


def max_bid(credits_left: int, required_left: int) -> int:
    """The most this lot may take without stranding an obligatory purchase. Pure.

    One credit reserved per slot still to fill beyond this one — which is FantaLab's own
    MAX, and the arithmetic behind ``docs/fantalab/00 §13``'s "invert it to learn a rival's
    remaining obligations". ``required_left`` counts the slots the band still owes,
    *including* the one this lot would fill, so the reserve is ``required_left - 1``.

    **Nothing enforces this but us.** ``docs/fantalab/01:142`` calls the MAX client-enforced,
    and ``docs/fantalab/06:389-412`` shows the RTDB rules validating only that the price
    exceeds the current one and names the right lot. A raise past the cap is accepted.

    The cap matters most exactly where the walk-away stops helping: ``reservations`` returns
    the whole remaining budget for a target whose removal makes the roster infeasible, which
    reads as "pay anything" with 28 slots still empty.
    """
    reserve = max(0, required_left - 1)
    return max(0, credits_left - reserve)


def _refusal(
    snapshot: Mapping[str, Any],
    seat: Seat,
    *,
    target: str,
    walk_away: int,
    remaining_budget: int,
    now_ms: int,
    step: int,
    max_cap: int | None = None,
) -> tuple[str | None, int]:
    """The guard that refuses a raise (a stable name), or ``None`` if one can be placed.

    Evaluated once and shared by ``decide_bid`` (which builds the payload when it returns
    ``None``) and ``pass_reason`` (which surfaces the name for the loop's per-guard counters), so
    the guard order lives in exactly one place. Also returns the next price, computed here.
    """
    player_id = snapshot.get("player_id")
    if not isinstance(player_id, str) or player_id != target:
        return ("not_target", 0)  # no lot, or not the player we're chasing
    if snapshot.get("asta_state") == "closed":
        return ("closed", 0)  # lot frozen; a raise would be rejected
    if snapshot.get("user_id") == seat.user_id:
        return ("already_high", 0)  # we hold the high bid — don't bid against ourselves

    last_bid_time = snapshot.get("last_bid_time")
    if (
        isinstance(last_bid_time, int)
        and not isinstance(last_bid_time, bool)
        and now_ms - last_bid_time <= FLOOR_MS
    ):
        return ("floor", 0)  # inside the 500 ms floor the room enforces

    current = snapshot.get("price")
    current_price = current if isinstance(current, int) and not isinstance(current, bool) else 0
    next_price = current_price + step
    if next_price > walk_away:
        return ("walk_away", next_price)  # crossing our ceiling
    if max_cap is not None and next_price > max_cap:
        # Before `budget`, and named separately: both would refuse a bid past the purse, and
        # the counter is the only thing that tells "we are out of money" from "we are keeping
        # enough back to finish the rosa".
        return ("max_cap", next_price)
    if next_price > remaining_budget:
        return ("budget", next_price)  # over budget
    return (None, next_price)


def decide_bid(
    snapshot: Mapping[str, Any],
    seat: Seat,
    fantaleague_id: str,
    *,
    target: str,
    walk_away: int,
    remaining_budget: int,
    now_ms: int,
    step: int = 1,
    max_cap: int | None = None,
) -> dict[str, Any] | None:
    """The raise to place on the current lot, or ``None`` to pass.

    ``snapshot`` is the live ``auction/<fl>`` (or ``assign/<fl>``) node. ``target`` is the player
    we want; ``walk_away`` the most we will pay for him; ``now_ms`` the injected clock (for the
    floor and ``last_update``). Returns a payload matching ``06 §5`` exactly, or ``None`` if any
    guard trips.
    """
    reason, next_price = _refusal(
        snapshot,
        seat,
        target=target,
        walk_away=walk_away,
        remaining_budget=remaining_budget,
        now_ms=now_ms,
        step=step,
        max_cap=max_cap,
    )
    if reason is not None:
        return None
    return {
        "price": next_price,
        "fantaleague_id": fantaleague_id,
        "user_id": seat.user_id,
        "fantateam_id": seat.fantateam_id,
        "player_id": target,
        "is_first": False,
        "update_type": "raise",
        "last_bid_time": SERVER_TIMESTAMP,
        "last_update": now_ms,
    }


def pass_reason(
    snapshot: Mapping[str, Any],
    seat: Seat,
    *,
    target: str,
    walk_away: int,
    remaining_budget: int,
    now_ms: int,
    step: int = 1,
    max_cap: int | None = None,
) -> str | None:
    """The name of the guard that would refuse a raise, or ``None`` if one can be placed. Pure."""
    return _refusal(
        snapshot,
        seat,
        target=target,
        walk_away=walk_away,
        remaining_budget=remaining_budget,
        now_ms=now_ms,
        step=step,
        max_cap=max_cap,
    )[0]


__all__ = ["FLOOR_MS", "SERVER_TIMESTAMP", "Seat", "decide_bid", "pass_reason"]
