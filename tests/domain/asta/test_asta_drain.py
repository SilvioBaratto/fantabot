"""Safe-drain suggestions: make rivals overpay, never misfire. Pure and synchronous.

The LLM decides *when* a player is one we don't want and the room is ripe; this computes
*how high* is safe. The cap is strictly below the player's worth to us — so even the worst
case (we win) leaves us owning him below value, never at a loss — and a push is only proposed
while enough rivals are still contesting to carry it past our cap.
"""

from __future__ import annotations

from fantabot.domain.asta.drain import DrainSuggestion, safe_push_cap, suggest_push


def test_cap_is_strictly_below_our_value() -> None:
    assert safe_push_cap(50.0) == 49
    assert safe_push_cap(1.0) == 0


def test_a_ripe_unwanted_player_gets_a_capped_push() -> None:
    got = suggest_push("p1", our_value=50.0, current_price=10, contesters=3, in_our_plan=False)
    assert got == DrainSuggestion(player_id="p1", cap=49, contesters=3)
    assert got.cap < 50  # the no-misfire guarantee


def test_we_never_push_a_player_we_want() -> None:
    assert suggest_push("p1", our_value=50.0, current_price=10, contesters=3, in_our_plan=True) is None


def test_we_do_not_push_without_enough_rivals_to_carry_it() -> None:
    # Only one rival contesting → pushing risks winning him ourselves.
    assert suggest_push("p1", our_value=50.0, current_price=10, contesters=1, in_our_plan=False) is None


def test_no_push_when_the_price_already_meets_the_safe_cap() -> None:
    # current 49, cap 49 → no safe room to raise.
    assert suggest_push("p1", our_value=50.0, current_price=49, contesters=3, in_our_plan=False) is None
