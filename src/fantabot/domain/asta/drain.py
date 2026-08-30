"""Safe-drain suggestions: bid up players we don't want to make rivals overpay. Pure.

Group dynamics, done safely. The LLM decides *when* a player is one we don't want and the
room is ripe; this module decides *how high* we may push. Two guarantees make it a "safe"
drain rather than a way to lose the asta:

1. The cap is **strictly below the player's worth to us** — so the worst case (nobody tops us
   and we win) still leaves us owning him below his value, never at a loss.
2. We only propose a push while **enough rivals are still contesting** to carry the price past
   our cap; pushing into an empty room is how you buy a player you didn't want.

The push is advisory in the MVP — it proposes the cap; the human raises. Execution is the
gated auto-bid phase.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Rivals that must still be contesting before a push is safe to propose.
DEFAULT_MIN_CONTESTERS = 2


@dataclass(frozen=True)
class DrainSuggestion:
    """A safe push: how high to bid on an unwanted player, and how contested he is."""

    player_id: str
    cap: int
    contesters: int


def safe_push_cap(our_value: float) -> int:
    """The highest safe bid: strictly below the player's worth to us (floored at 0)."""
    return max(0, int(our_value) - 1)


def suggest_push(
    player_id: str,
    *,
    our_value: float,
    current_price: int,
    contesters: int,
    in_our_plan: bool,
    min_contesters: int = DEFAULT_MIN_CONTESTERS,
) -> DrainSuggestion | None:
    """Propose a safe capped push, or ``None`` if it would not be safe or worthwhile.

    Returns ``None`` when the player is one we actually want, when too few rivals are
    contesting to carry the price, or when the price already meets the safe cap.
    """
    if in_our_plan:
        return None  # we want him — never drive up our own target
    if contesters < min_contesters:
        return None  # not enough rivals to top us — pushing risks owning him
    cap = safe_push_cap(our_value)
    if cap < current_price + 1:
        return None  # no safe room left to raise
    return DrainSuggestion(player_id=player_id, cap=cap, contesters=contesters)
