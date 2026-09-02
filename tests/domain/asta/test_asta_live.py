"""Live room read: raw FantaLab states -> normalized assignment events. Pure/sync.

A sale is a ``close_auction`` state. The pure normalizer is what the rolling optimizer
reacts to; the async subscription to a real room is a thin documented shell (the own-room
feed path is still an open question), so nothing here opens a socket.
"""

from __future__ import annotations

from fantabot.domain.asta.live import (
    AssignmentEvent,
    attribute_passed_lots,
    normalize,
    parse_assignment,
    parse_purchase,
    purchases_to_events,
)


def _close(player_id: object, price: object, buyer: object = "t5") -> dict[str, object]:
    return {
        "update_type": "close_auction",
        "player_id": player_id,
        "price": price,
        "fantateam_id": buyer,
    }


def test_a_close_state_becomes_an_assignment_event() -> None:
    event = parse_assignment(_close("123", 42, "t5"))
    assert event == AssignmentEvent(player_id="123", price=42, buyer_team_id="t5")


def test_non_close_states_are_not_events() -> None:
    for update_type in ("first_call", "raise", "confirm", "reset"):
        assert parse_assignment({"update_type": update_type, "player_id": "1", "price": 5}) is None


def test_a_close_without_player_or_price_is_ignored() -> None:
    assert parse_assignment(_close(None, 42)) is None
    assert parse_assignment(_close("123", None)) is None


def test_a_sale_with_no_named_buyer_keeps_a_none_buyer() -> None:
    assert parse_assignment(_close("123", 10, buyer=None)).buyer_team_id is None


def test_normalize_keeps_only_the_sales_in_order() -> None:
    states = [
        {"update_type": "first_call", "player_id": "1", "price": 1},
        _close("1", 30, "a"),
        {"update_type": "raise", "player_id": "2", "price": 5},
        _close("2", 8, "b"),
    ]
    events = normalize(states)
    assert [e.player_id for e in events] == ["1", "2"]
    assert [e.price for e in events] == [30, 8]


# --- the live path: the purchases/<fl> ledger, the authoritative sale signal ---


def _purchase(
    player_id: str, price: int, buyer: object, created_at: int, user: object = "u1",
) -> dict[str, object]:
    rec: dict[str, object] = {
        "player_id": player_id, "price": price, "created_at": created_at, "user_id": user,
    }
    if buyer is not None:
        rec["fantateam_id"] = buyer
    return rec


def test_a_purchase_record_becomes_an_assignment_event() -> None:
    event = parse_purchase(_purchase("kean", 16, "seat2", 100, user="u9"))
    assert event == AssignmentEvent(
        player_id="kean", price=16, buyer_team_id="seat2", bidder_user_id="u9"
    )


def test_an_unsold_lot_keeps_a_none_buyer_and_is_not_dropped() -> None:
    # a skip is a real record: price 0, no fantateam_id
    event = parse_purchase(_purchase("orsolini", 0, None, 50, user="admin"))
    assert event == AssignmentEvent(
        player_id="orsolini", price=0, buyer_team_id=None, bidder_user_id="admin"
    )


def test_a_skip_with_no_user_id_at_all_carries_none() -> None:
    record = _purchase("orsolini", 0, None, 50)
    del record["user_id"]
    assert parse_purchase(record) == AssignmentEvent("orsolini", 0, None, None)


def test_a_garbled_purchase_is_ignored() -> None:
    assert parse_purchase({"price": 5}) is None  # no player_id
    assert parse_purchase({"player_id": "x", "price": True}) is None  # bool is not a price


def test_purchases_to_events_orders_by_created_at() -> None:
    ledger = {
        "p2": _purchase("b", 8, "t2", 200),
        "p1": _purchase("a", 30, "t1", 100),
        "p3": _purchase("c", 1, "t3", 300),
    }
    events = purchases_to_events(ledger)
    assert [e.player_id for e in events] == ["a", "b", "c"]
    assert [e.price for e in events] == [30, 8, 1]


def test_a_reopened_close_is_no_phantom_sale() -> None:
    # a lot closed then RIAPRI'd never writes a purchase, so the ledger holds only the real
    # final sale — keying off purchases cannot double-count it.
    ledger = {"only": _purchase("kean", 16, "seat2", 100)}
    assert purchases_to_events(ledger) == [AssignmentEvent("kean", 16, "seat2", "u1")]


# --- B: a passed lot is invisible to the ledger --------------------------------------------

ADMIN = "admin-uid"
SEATS = {"us": "team-us", "rival": "team-rival"}


class TestAttributePassedLots:
    """`parse_purchase` already emits an unsold lot as `price=0, buyer_team_id=None` — real,
    and not dropped. What it cannot tell apart on its own is *why*: the room admin auto-
    skipping an expired lot writes the identical shape as a raise that stood but the admin
    passed anyway. Only `bidder_user_id` (Task 2.1) tells them apart.
    """

    def test_a_stood_raise_the_admin_passed_is_reattributed(self) -> None:
        events = [AssignmentEvent("kean", 0, None, "us")]

        attributed, reclaimed = attribute_passed_lots(
            events, admin_user_id=ADMIN, seat_by_user=SEATS, price_of={},
        )

        assert attributed == [AssignmentEvent("kean", 1, "team-us", "us")]
        assert reclaimed == ["kean"]

    def test_the_reclaimed_price_prefers_an_observed_clearing_price_over_the_minimum(
        self,
    ) -> None:
        events = [AssignmentEvent("kean", 0, None, "us")]

        attributed, _ = attribute_passed_lots(
            events, admin_user_id=ADMIN, seat_by_user=SEATS, price_of={"kean": 12.0},
        )

        assert attributed[0].price == 12

    def test_the_rule_is_exact_an_admin_stamped_skip_is_never_claimed(self) -> None:
        """The one thing this task must never get wrong: however many of these there are —
        248 on the real evening — none is a stood raise."""
        events = [AssignmentEvent("kean", 0, None, ADMIN)]

        attributed, reclaimed = attribute_passed_lots(
            events, admin_user_id=ADMIN, seat_by_user=SEATS, price_of={},
        )

        assert attributed == events
        assert reclaimed == []

    def test_a_real_skip_with_no_bidder_at_all_is_left_alone(self) -> None:
        events = [AssignmentEvent("kean", 0, None, None)]

        attributed, reclaimed = attribute_passed_lots(
            events, admin_user_id=ADMIN, seat_by_user=SEATS, price_of={},
        )

        assert attributed == events
        assert reclaimed == []

    def test_a_sold_lot_is_never_touched(self) -> None:
        """The rule keys on `buyer_team_id is None` — a real sale already has one and must
        pass through byte-for-byte, whoever `bidder_user_id` names."""
        events = [AssignmentEvent("kean", 40, "team-rival", "rival")]

        attributed, reclaimed = attribute_passed_lots(
            events, admin_user_id=ADMIN, seat_by_user=SEATS, price_of={},
        )

        assert attributed == events
        assert reclaimed == []

    def test_a_bidder_with_no_known_seat_holds_rather_than_raising(self) -> None:
        """A stale or unseated uid is not the room's fault to crash over — the same "hold,
        don't end the evening" convention the rest of this package keeps."""
        events = [AssignmentEvent("kean", 0, None, "nobody-holds-this-seat")]

        attributed, reclaimed = attribute_passed_lots(
            events, admin_user_id=ADMIN, seat_by_user=SEATS, price_of={},
        )

        assert attributed == events
        assert reclaimed == []


class TestThePassedLotFixtureFromTheRealEvening:
    """Distilled from `data/room_state_snapshot.jsonl` — the room's own live `purchases/<fl>`
    ledger for 2026-09-01's "è morto malen" evening — rather than a synthesized shape, because
    the acceptance is a real reconciliation: `data/asta_2026-09-01_riepilogo.txt` recorded
    30/30 slots and 474 credits spent, all of it confirmed on the platform, while the bot's own
    ledger fold that evening (missing this fix) saw only 28 owned and 472 spent — exactly
    Sohm and Caprile short, both bought at 1 credit and both passed by the admin after our own
    raise had stood.

    The fixture carries 28 of our real sold records (summing to 472, matching the gap exactly),
    one rival's real sold record, the 4 real zero-price records whose `user_id` names a bidder
    rather than the admin (2 ours — Sohm, Caprile — 2 belonging to two different rivals, the
    same defect happening elsewhere in the same room), and 6 of the real evening's 248 admin
    auto-skips, to prove those are never claimed even sitting right beside the ones that should
    be.
    """

    OUR_TEAM = "3097845d-6d44-42e9-9668-37803806036e"
    OUR_USER = "fee799b2-1351-4695-b1f8-79d6ace8a4e6"
    ADMIN_USER = "c95023fa-fa42-4d56-90d6-cd1eca955eb3"
    RIVAL_A_TEAM = "9a5a46fb-12ba-4b5c-80ea-67e9d31f04d1"
    RIVAL_A_USER = "06c47965-5ea3-48e6-84f1-4947d30f2da6"
    RIVAL_B_TEAM = "892ddd6c-d2f4-491b-a217-a6fa743a2123"
    RIVAL_B_USER = "675ad9b6-3c8d-4617-b4d0-e3134d3aa779"

    @classmethod
    def _events(cls):  # type: ignore[no-untyped-def]
        import json
        from pathlib import Path

        ledger = json.loads(
            (Path(__file__).parents[2] / "golden" / "passed_lots_2026_09_01.json")
            .read_text(encoding="utf-8")
        )
        return purchases_to_events(ledger)

    def test_without_the_fix_the_fold_is_two_players_and_two_credits_short(self) -> None:
        """The bug, pinned: the ledger's own `buyer_team_id` says 28 owned, 472 spent — not
        the 30/474 the platform actually holds."""
        from fantabot.domain.asta.reservation import apply_event
        from fantabot.domain.asta.state import AstaState

        state = AstaState(total_budget=500.0)
        for event in self._events():
            state = apply_event(state, event, our_team_id=self.OUR_TEAM)

        assert len(state.owned) == 28
        assert state.spent == 472.0

    def test_the_fix_reconciles_to_the_platform_s_real_30_slots_and_474_credits(self) -> None:
        from fantabot.domain.asta.reservation import apply_event
        from fantabot.domain.asta.state import AstaState

        seat_by_user = {
            self.OUR_USER: self.OUR_TEAM,
            self.RIVAL_A_USER: self.RIVAL_A_TEAM,
            self.RIVAL_B_USER: self.RIVAL_B_TEAM,
        }
        attributed, reclaimed = attribute_passed_lots(
            self._events(), admin_user_id=self.ADMIN_USER, seat_by_user=seat_by_user,
            price_of={},
        )

        state = AstaState(total_budget=500.0)
        for event in attributed:
            state = apply_event(state, event, our_team_id=self.OUR_TEAM)

        assert len(state.owned) == 30
        assert state.spent == 474.0
        assert len(reclaimed) == 4, "2 ours (Sohm, Caprile) and 2 belonging to two rivals"

    def test_the_six_sampled_admin_skips_are_never_among_the_reclaimed(self) -> None:
        """However many admin auto-skips sit in the same ledger, none of them is claimed."""
        attributed, reclaimed = attribute_passed_lots(
            self._events(), admin_user_id=self.ADMIN_USER,
            seat_by_user={self.OUR_USER: self.OUR_TEAM}, price_of={},
        )

        admin_authored = [
            e.player_id for e in self._events() if e.bidder_user_id == self.ADMIN_USER
        ]
        assert len(admin_authored) == 6
        assert not (set(admin_authored) & set(reclaimed))
        for event in attributed:
            if event.player_id in admin_authored:
                assert event.buyer_team_id is None
