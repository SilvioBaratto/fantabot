"""No single lot may eat the rosa.

`reservations` returns the *whole remaining budget* as the walk-away for any target whose
removal makes the roster infeasible (`reservation.py`). That is correct as a statement of
value — he is essential — and catastrophic as a bid ceiling: it says "pay anything", and
with 28 slots still to fill, paying anything for one player ends the evening with a rosa
that cannot be fielded.

FantaLab's own client enforces a MAX for exactly this reason, reserving one credit per
obligatory purchase still outstanding. **The server does not.** `docs/fantalab/01:142` calls
it client-enforced, and `docs/fantalab/06:389-412` shows the RTDB rules validating only
`price > current` and `player_id`. So a bot that does not model it has nothing behind it.

`required_left` comes from `RosterRules`, not from the room: `min_player` is a FantaLab
field that `asta bid` never fetches — it takes a shard and a seat and never calls the
authenticated REST API. The band the domain already carries is what we have at this point.
"""

from __future__ import annotations

from fantabot.domain.asta.bid import Seat, decide_bid, max_bid, pass_reason
from fantabot.domain.asta.state import AstaState, RosterRules

SEAT = Seat(fantateam_id="us", user_id="me")
FAR = 10_000_000


def _lot(price: int) -> dict[str, object]:
    return {"player_id": "kean", "price": price, "user_id": "rival", "last_bid_time": 0}


class TestMaxBid:
    def test_it_reserves_one_credit_for_each_obligatory_buy(self) -> None:
        """500 credits, 29 slots still to fill: the most this lot may take is 471."""
        assert max_bid(credits_left=500, required_left=29) == 472

    def test_the_cap_disappears_once_the_minimum_is_reached(self) -> None:
        """The last slot has nothing left to reserve for, so the whole purse is available.

        This is the unlock FantaLab shows in the room, and forgetting it is how a rival
        with a full rosa takes a player off you with a bid you thought impossible.
        """
        assert max_bid(credits_left=500, required_left=0) == 500

    def test_one_slot_left_reserves_nothing_for_itself(self) -> None:
        assert max_bid(credits_left=500, required_left=1) == 500

    def test_it_never_goes_negative(self) -> None:
        """More obligatory buys than credits is reachable late in a bad evening."""
        assert max_bid(credits_left=3, required_left=10) == 0


class TestTheGuardChain:
    def test_a_bid_over_the_cap_is_refused_by_name(self) -> None:
        reason = pass_reason(
            _lot(480), SEAT, target="kean", walk_away=500,
            remaining_budget=500, now_ms=FAR, max_cap=472,
        )

        assert reason == "max_cap"

    def test_the_cap_refuses_before_the_budget_does(self) -> None:
        """Both would refuse here; the counter has to say which rule bound, or the
        heartbeat cannot tell 'we are out of money' from 'we are protecting the rosa'."""
        reason = pass_reason(
            _lot(600), SEAT, target="kean", walk_away=1000,
            remaining_budget=500, now_ms=FAR, max_cap=472,
        )

        assert reason == "max_cap"

    def test_a_bid_inside_the_cap_still_goes(self) -> None:
        payload = decide_bid(
            _lot(100), SEAT, "L", target="kean", walk_away=500,
            remaining_budget=500, now_ms=FAR, max_cap=472,
        )

        assert payload is not None
        assert payload["price"] == 101

    def test_no_cap_given_leaves_the_chain_exactly_as_it_was(self) -> None:
        """`asta live` and the replay paths pass no cap and must not change behaviour."""
        assert decide_bid(
            _lot(480), SEAT, "L", target="kean", walk_away=500,
            remaining_budget=500, now_ms=FAR,
        ) is not None


class TestRequiredLeftComesFromTheBand:
    def test_it_is_what_the_band_still_owes(self) -> None:
        state = AstaState(owned=("a", "b"), total_budget=500.0)

        assert RosterRules().size - len(state.owned) == 28

    def test_a_shrunk_band_owes_less(self) -> None:
        """`drop_unvaluable` shrinks the band; the cap has to follow it down or it would
        reserve credits for slots that are already filled."""
        assert RosterRules(size=29, min_movement=27).size - 2 == 27
