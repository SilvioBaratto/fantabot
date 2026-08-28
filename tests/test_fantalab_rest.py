"""The own-room REST reads, on `httpx.MockTransport`. **Zero sockets.**

`fantalab/rest.py` wraps the two unauthenticated reads the live advisory needs — one league
record and the public live list — so the parse is pinned on fixtures and the HTTP call is
proven to send the right shape without opening a socket (the autouse guard in `conftest` would
fail the test if it did). The shard resolution is the load-bearing detail: a room's `db` field
routes every realtime subscription, and `null` means the default namespace, not shard 0.
"""

from __future__ import annotations

from typing import Any

import httpx

from fantabot.fantalab import rest

# The shape observed live 2026-08-28 for the throwaway "provaalgoritmo" room (docs/fantalab/06).
FETCH_BODY: dict[str, Any] = {
    "fantaleague_id": "90c5fa2c-league",
    "admin_id": "a2292bc1-admin",
    "fantaleague_name": "provaalgoritmo",
    "num_teams": 4,
    "num_credits": 500,
    "asta_type": "mantra",
    "asta_mode": "random",
    "raise_mode": "free",
    "season": "s_24_25",
    "counter_time": 10,
    "counter_time_first": 20,
    "is_live": False,
    "auction_running": True,
    "db": "9",
    "fantateams": [
        {"fantateam_id": "c90c-seat1", "user_id": "a2292bc1-admin", "position": 1,
         "team_name": "Nome del tuo team", "max_credits": 500},
        {"fantateam_id": "80c4-seat2", "user_id": None, "position": 2,
         "team_name": "Team #2", "max_credits": 500},
    ],
}


def test_shard_of_folds_string_int_and_default() -> None:
    assert rest.shard_of("9") == 9
    assert rest.shard_of(9) == 9
    assert rest.shard_of(None) is None      # the default namespace, NOT shard 0
    assert rest.shard_of("") is None
    assert rest.shard_of("not-a-number") is None
    assert rest.shard_of(True) is None      # bool is an int subclass; never a shard


def test_parse_league_pins_shard_seats_timers_and_modes() -> None:
    room = rest.parse_league(FETCH_BODY)
    assert room.db == 9
    assert room.raise_mode == "free"
    assert room.asta_mode == "random"
    assert room.asta_type == "mantra"
    assert (room.counter_time, room.counter_time_first) == (10, 20)
    assert room.num_teams == 4 and room.num_credits == 500
    assert room.auction_running is True and room.is_live is False
    assert len(room.seats) == 2
    seat2 = room.seats[1]
    assert (seat2.fantateam_id, seat2.user_id, seat2.position) == ("80c4-seat2", None, 2)
    # a free seat is one nobody holds
    assert room.free_seats() == (seat2,)


def test_parse_league_null_db_is_default_namespace() -> None:
    assert rest.parse_league({**FETCH_BODY, "db": None}).db is None


def test_fetch_league_posts_the_right_shape_without_a_socket() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=FETCH_BODY)

    room = rest.fetch_league("90c5fa2c-league", transport=httpx.MockTransport(handler))

    assert seen["method"] == "POST"
    assert seen["path"] == "/fantaleague/fetch"
    assert seen["body"] == {"fantaleague_id": "90c5fa2c-league", "type": "fantaleague"}
    assert room.db == 9 and room.raise_mode == "free" and len(room.seats) == 2


def test_live_leagues_parses_a_bare_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fantaleagues/live"
        return httpx.Response(200, json=[FETCH_BODY, {**FETCH_BODY, "db": None}])

    rooms = rest.live_leagues(transport=httpx.MockTransport(handler))
    assert [r.db for r in rooms] == [9, None]


def test_join_team_posts_only_seat_and_user() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": "Fantateam Joined"})

    ok = rest.join_team("80c4-seat2", "my-uid", transport=httpx.MockTransport(handler))

    assert ok is True
    assert seen["path"] == "/fantaleague/join"
    # invitation_id is not required when the seat id is known (06 §3)
    assert seen["body"] == {"fantateam_id": "80c4-seat2", "user_id": "my-uid"}
