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

from fantabot.adapters.http.fantalab import rest

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


def test_a_token_is_sent_as_a_bearer_and_omitted_without_one() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=FETCH_BODY)

    rest.fetch_league("L", token="tok-123", transport=httpx.MockTransport(handler))
    assert seen["auth"] == "Bearer tok-123"

    rest.fetch_league("L", transport=httpx.MockTransport(handler))
    assert seen["auth"] is None  # no token -> no Authorization header


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


class TestOurSeatInTheRoom:
    """Four id spaces are in play, and only one of them is our seat.

    `docs/lega-legamiallerotaie2.md` records a leghe.fantacalcio.it team id (`10000003`) and
    user id; FantaLab uses uuids for both. The RTDB validates neither — a bid naming a foreign
    seat is accepted with a 200 (`docs/fantalab/06 §10.1`, test 7) — so a mistyped or
    wrong-space id does not fail, it quietly drives somebody else's team.

    That is why the seat is looked up rather than typed: our FantaLab uuid is already in the
    stored session, and matching it against the room is the only step that cannot go wrong
    silently.
    """

    def _room(self):  # type: ignore[no-untyped-def]
        return rest.parse_league({
            "fantaleague_id": "L",
            "fantateams": [
                {"fantateam_id": "t1", "user_id": "someone-else", "team_name": "Rivali"},
                {"fantateam_id": "t2", "user_id": "us-uuid", "team_name": "Team C"},
                {"fantateam_id": "t3", "user_id": None, "team_name": "Libera"},
            ],
        })

    def test_it_finds_the_seat_holding_our_uuid(self) -> None:
        seat = self._room().seat_of("us-uuid")

        assert seat is not None
        assert seat.fantateam_id == "t2"
        assert seat.team_name == "Team C"

    def test_an_unseated_user_gets_none_rather_than_a_guess(self) -> None:
        assert self._room().seat_of("nobody") is None

    def test_a_free_seat_is_never_returned_as_ours(self) -> None:
        """`user_id is None` marks a free seat. Matching `None` against an absent uid would
        hand us the first empty chair in the room."""
        assert self._room().seat_of(None) is None  # type: ignore[arg-type]

    def test_the_free_seats_are_still_listed_for_an_operator_who_is_not_in_yet(self) -> None:
        assert [s.fantateam_id for s in self._room().free_seats()] == ["t3"]


class TestTheRosterBandFields:
    """`min_player` is what the MAX cap reserves against, and it is often absent.

    Measured over today's live registry: 100 of 159 Mantra rooms carry no `min_player` at all.
    Parsing it as `None` rather than defaulting is what lets the cap fail closed on a known
    band instead of silently reserving nothing.
    """

    def test_the_band_and_the_opening_price_rule_are_parsed(self) -> None:
        room = rest.parse_league({
            "fantaleague_id": "L",
            "min_player": 25,
            "max_player": 30,
            "call_at_quotaz": True,
        })

        assert (room.min_player, room.max_player) == (25, 30)
        assert room.call_at_quotaz is True

    def test_an_absent_band_is_none_not_a_default(self) -> None:
        room = rest.parse_league({"fantaleague_id": "L"})

        assert room.min_player is None
        assert room.max_player is None

    def test_call_at_quotaz_absent_reads_as_false(self) -> None:
        """FantaLab's default is "start from 1". A room that does not say is that room."""
        assert rest.parse_league({"fantaleague_id": "L"}).call_at_quotaz is False

    def test_players_settings_data_is_parsed_for_a_classic_static_room(self) -> None:
        room = rest.parse_league({
            "fantaleague_id": "L",
            "number_of_players_selection": "static",
            "players_settings_data": {"P": 3, "D": 8, "C": 8, "A": 6},
        })
        assert room.players_settings_data == {"P": 3, "D": 8, "C": 8, "A": 6}

    def test_absent_players_settings_data_is_none(self) -> None:
        assert rest.parse_league({"fantaleague_id": "L"}).players_settings_data is None


class TestTheGoalkeeperOutfieldBand:
    """`number_of_players_selection` and the `min-max-goalie-others` band it names — the wire
    key names here are a permissive guess (`docs/fantalab/01-auction-engine.md`, marked ⏳),
    not a confirmed contract, so each candidate is proven independently rather than assumed.
    """

    def test_the_selection_mode_is_parsed(self) -> None:
        room = rest.parse_league({
            "fantaleague_id": "L", "number_of_players_selection": "min-max-goalie-others",
        })

        assert room.number_of_players_selection == "min-max-goalie-others"

    def test_the_primary_snake_case_keys_are_read(self) -> None:
        room = rest.parse_league({
            "fantaleague_id": "L",
            "min_goalkeepers": 3, "max_goalkeepers": 3,
            "min_others": 23, "max_others": 28,
        })

        assert (room.min_goalkeepers, room.max_goalkeepers) == (3, 3)
        assert (room.min_others, room.max_others) == (23, 28)

    def test_the_italian_alias_is_tried_when_the_primary_key_is_absent(self) -> None:
        room = rest.parse_league({
            "fantaleague_id": "L",
            "min_portieri": 2, "max_portieri": 4, "min_altri": 20, "max_altri": 26,
        })

        assert (room.min_goalkeepers, room.max_goalkeepers) == (2, 4)
        assert (room.min_others, room.max_others) == (20, 26)

    def test_the_camel_case_alias_is_tried_when_neither_other_key_is_present(self) -> None:
        room = rest.parse_league({
            "fantaleague_id": "L",
            "minGoalkeepers": 1, "maxGoalkeepers": 5, "minOthers": 24, "maxOthers": 27,
        })

        assert (room.min_goalkeepers, room.max_goalkeepers) == (1, 5)
        assert (room.min_others, room.max_others) == (24, 27)

    def test_the_primary_key_wins_when_more_than_one_candidate_is_present(self) -> None:
        """A malformed or stale alias must never override the key the docs actually name."""
        room = rest.parse_league({
            "fantaleague_id": "L", "min_goalkeepers": 3, "min_portieri": 99,
        })

        assert room.min_goalkeepers == 3

    def test_none_of_the_candidates_present_is_none_not_a_default(self) -> None:
        room = rest.parse_league({"fantaleague_id": "L"})

        assert room.number_of_players_selection is None
        assert room.min_goalkeepers is None
        assert room.max_goalkeepers is None
        assert room.min_others is None
        assert room.max_others is None
