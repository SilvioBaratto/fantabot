"""The pure bid decision. No socket, no clock — every guard tested with fakes.

``decide_bid`` is the one place a bid is *shaped*; a boundary test pins that it imports nothing
that could reach the network or the database, exactly as the repo requires of its decision logic.
The payload it emits must match ``docs/fantalab/06-asta-write-path.md`` §5 to the field.
"""

from __future__ import annotations

import ast
from typing import Any

from _paths import pkg

from fantabot.asta_engine.bid import SERVER_TIMESTAMP, Seat, decide_bid, pass_reason

SEAT = Seat(fantateam_id="seat2", user_id="me")
NOW = 1_000_000
FL = "L"


def _lot(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "player_id": "kean",
        "price": 5,
        "user_id": "rival",
        "asta_state": None,
        "last_bid_time": 0,
    }
    base.update(over)
    return base


def _bid(snapshot: dict[str, Any], **kw: Any) -> dict[str, Any] | None:
    opts: dict[str, Any] = {"target": "kean", "walk_away": 30, "remaining_budget": 100, "now_ms": NOW}
    opts.update(kw)
    return decide_bid(snapshot, SEAT, FL, **opts)


def test_a_clean_raise_matches_the_documented_payload() -> None:
    payload = _bid(_lot(price=5))
    assert payload == {
        "price": 6,  # current + step(1)
        "fantaleague_id": FL,
        "user_id": "me",
        "fantateam_id": "seat2",
        "player_id": "kean",
        "is_first": False,
        "update_type": "raise",
        "last_bid_time": SERVER_TIMESTAMP,
        "last_update": NOW,
    }


def test_step_is_added_to_the_current_price() -> None:
    assert _bid(_lot(price=10), step=5)["price"] == 15


def test_pass_when_not_the_target_or_no_lot() -> None:
    assert _bid(_lot(player_id="someone_else")) is None
    assert _bid(_lot(player_id=None)) is None


def test_pass_on_a_closed_lot() -> None:
    assert _bid(_lot(asta_state="closed")) is None


def test_pass_when_we_already_hold_the_high_bid() -> None:
    assert _bid(_lot(user_id="me")) is None


def test_pass_inside_the_500ms_floor() -> None:
    assert _bid(_lot(last_bid_time=NOW - 200)) is None      # 200ms ago -> too soon
    assert _bid(_lot(last_bid_time=NOW - 800)) is not None  # 800ms ago -> fine


def test_pass_at_or_above_the_walk_away() -> None:
    # current 30, +1 = 31 > walk_away 30 -> pass
    assert _bid(_lot(price=30), walk_away=30) is None
    # current 29, +1 = 30 == walk_away 30 -> the last legal bid
    assert _bid(_lot(price=29), walk_away=30)["price"] == 30


def test_pass_when_over_budget() -> None:
    assert _bid(_lot(price=40), walk_away=100, remaining_budget=40) is None


def test_a_fresh_lot_with_no_price_bids_one() -> None:
    assert _bid(_lot(price=None, user_id=None))["price"] == 1


def test_pass_reason_names_the_guard_that_refuses() -> None:
    def reason(snapshot: dict[str, Any], **kw: Any) -> str | None:
        opts: dict[str, Any] = {
            "target": "kean", "walk_away": 30, "remaining_budget": 100, "now_ms": NOW,
        }
        opts.update(kw)
        return pass_reason(snapshot, SEAT, **opts)

    assert reason(_lot(price=5)) is None  # a bid can be placed
    assert reason(_lot(player_id="x")) == "not_target"
    assert reason(_lot(asta_state="closed")) == "closed"
    assert reason(_lot(user_id="me")) == "already_high"
    assert reason(_lot(last_bid_time=NOW - 100)) == "floor"
    assert reason(_lot(price=30), walk_away=30) == "walk_away"
    assert reason(_lot(price=40), walk_away=100, remaining_budget=40) == "budget"


def test_bid_module_imports_no_io() -> None:
    path = pkg("asta_engine") / "bid.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"httpx", "socket", "playwright"}
    assert not (imported & forbidden), f"bid.py must stay pure; found {imported & forbidden}"
    assert not any(name.startswith("fantabot.db") for name in imported), "bid.py reaches the DB"
