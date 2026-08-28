"""Live room read: raw FantaLab states -> normalized assignment events. Pure/sync.

A sale is a ``close_auction`` state. The pure normalizer is what the rolling optimizer
reacts to; the async subscription to a real room is a thin documented shell (the own-room
feed path is still an open question), so nothing here opens a socket.
"""

from __future__ import annotations

from fantabot.asta_engine.live import AssignmentEvent, normalize, parse_assignment


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
