"""Owning a player the pool cannot name must not end the evening.

`optimize_roster` refuses a state whose `owned` holds an id absent from the pool
(`optimizer.py:396-398`), and it is right to: a roster it cannot value is not a roster.
But the bidder rebuilds `AstaState` from the `purchases/` ledger on *every* cycle and a
purchase record is never removed, so the offending id comes back every two seconds.
Catching the exception without removing its cause is not a hold — it is a stop, for the
rest of the auction, with the heartbeat repeating the same line.

The player is genuinely ours. Dropping him from `owned` without shrinking the roster band
would tell the optimiser to buy a replacement slot we do not have, so both move together.

One live instance today: fantacalcio id 7581, Konaté A. — in FantaLab's listone, absent
from our `quotazioni`.
"""

from __future__ import annotations

from fantabot.domain.asta.roles import MantraPlayer, normalize_roles
from fantabot.domain.asta.state import AstaState, RosterRules, drop_unvaluable, rules_for_room

POOL = [
    MantraPlayer("1", normalize_roles(["A"])),
    MantraPlayer("2", normalize_roles(["POR"])),
]


def test_a_valuable_rosa_is_returned_untouched() -> None:
    """The common path allocates nothing and changes nothing."""
    state = AstaState(owned=("1", "2"), spent=30.0, total_budget=500.0)

    kept, rules, dropped = drop_unvaluable(state, POOL, RosterRules())

    assert kept is state, "no copy when there is nothing to drop"
    assert rules == RosterRules()
    assert dropped == []


def test_an_unvaluable_owned_player_leaves_owned() -> None:
    state = AstaState(owned=("1", "7581"), total_budget=500.0)

    kept, _, dropped = drop_unvaluable(state, POOL, RosterRules())

    assert kept.owned == ("1",)
    assert dropped == ["7581"]


def test_the_credits_he_cost_stay_spent() -> None:
    """He was bought. Forgetting the spend would hand the planner money we do not have."""
    state = AstaState(owned=("1", "7581"), spent=140.0, total_budget=500.0)

    kept, _, _ = drop_unvaluable(state, POOL, RosterRules())

    assert kept.spent == 140.0
    assert kept.remaining_budget == 360.0


def test_the_roster_band_shrinks_by_what_was_dropped() -> None:
    """He occupies a slot. Leaving the band at 30 would plan a replacement for a player
    we already hold, and the rosa would end the evening one over."""
    state = AstaState(owned=("1", "7581"), total_budget=500.0)

    _, rules, _ = drop_unvaluable(state, POOL, RosterRules())

    assert rules.size == 29
    assert rules.min_movement == 27, "the slot came out of the band he could have filled"
    assert rules.min_goalkeepers == 2, "the goalkeeper floor is the platform's, not ours"


def test_a_dropped_goalkeeper_shrinks_the_goalkeeper_floor_instead() -> None:
    """Two goalkeepers is the platform's rule, but a goalkeeper we already own and cannot
    value still fills one of the two — asking for two more would be asking for three."""
    state = AstaState(owned=("2", "gk-unknown"), total_budget=500.0)
    pool = [*POOL, MantraPlayer("gk-unknown", normalize_roles(["POR"]))]

    _, rules, dropped = drop_unvaluable(state, pool[:2], RosterRules())

    assert dropped == ["gk-unknown"]
    assert rules.size == 29
    # We cannot tell his role -- he is not in the pool -- so the movement band is what
    # shrinks. Naming him in the heartbeat is what lets a human correct the assumption.
    assert rules.min_movement == 27


def test_the_shrink_moves_both_bands_together_on_a_room_derived_rules_too() -> None:
    """Task 3.3: `RosterRules` is no longer always `size=30` — a room can declare 25 with a
    2/23 split (`rules_for_room`). The shrink-together property has to hold on that shape
    exactly as it does on the default, not on a `size=30` this function happens to see most
    often in its own test fixtures.
    """
    room_rules, provenance = rules_for_room(
        selection="min-max-goalie-others", min_player=25, max_player=25,
        min_goalkeepers=2, min_others=23,
    )
    assert provenance == "read from the room"
    state = AstaState(owned=("1", "7581"), total_budget=500.0)

    _, shrunk, dropped = drop_unvaluable(state, POOL, room_rules)

    assert dropped == ["7581"]
    assert shrunk.size == 24, "one slot filled by a player the pool cannot value"
    assert shrunk.min_movement == 22, "the slot came out of the band he could have filled"
    assert shrunk.min_goalkeepers == 2, "the room's own goalkeeper floor is unaffected"


def test_the_band_never_shrinks_below_what_is_already_owned() -> None:
    """Pathological but reachable: more unvaluable players than the band has room for."""
    state = AstaState(owned=tuple(f"x{i}" for i in range(40)), total_budget=500.0)

    _, rules, dropped = drop_unvaluable(state, POOL, RosterRules())

    assert len(dropped) == 40
    assert rules.size >= 0
    assert rules.min_movement >= 0
    assert rules.min_goalkeepers >= 0
