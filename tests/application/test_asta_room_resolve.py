"""A pasted link and a stored uid become a room we can bid in — or a refusal that says why.

Everything here is injected. `resolve_room` takes the fetch as a callable, so this file drives
it with a dict and opens nothing; the interface binds the real `rest.fetch_league` and the
Bearer, which never crosses into this layer.

⚠ `rest.fetch_league` has **never** been executed against a real room (it had no caller in
`src/` at all before this phase). These tests pin the shape we believe it returns; the first
live `--resolve-only` run is what confirms it.
"""

from __future__ import annotations

import pytest

from fantabot.adapters.http.fantalab.rest import parse_league
from fantabot.application.asta_room import RoomRefused, resolve_room

BODY = {
    "fantaleague_id": "L",
    "db": 9,
    "asta_type": "mantra",
    "asta_mode": "chiamata",
    "raise_mode": "free",
    "num_teams": 8,
    "num_credits": 500,
    "min_player": 30,
    "max_player": 30,
    "admin_id": "rival",
    "fantateams": [
        {"fantateam_id": "t1", "user_id": "rival", "team_name": "Rivali"},
        {"fantateam_id": "t2", "user_id": "us", "team_name": "Team C"},
        {"fantateam_id": "t3", "user_id": None, "team_name": "Libera"},
    ],
}


def _resolve(body=None, user_id="us"):  # type: ignore[no-untyped-def]
    raw = body if body is not None else BODY
    return resolve_room("L", user_id=user_id, fetch=lambda _fl: parse_league(raw))


class TestWhatComesBack:
    def test_the_shard_the_seat_and_the_budget(self) -> None:
        room = _resolve()

        assert room.db == 9
        assert room.seat is not None
        assert room.seat.fantateam_id == "t2"
        assert room.num_credits == 500

    def test_shard_none_stays_none_rather_than_becoming_zero(self) -> None:
        """`None` is the default RTDB namespace, and shard 0 is a different database."""
        room = _resolve({**BODY, "db": None})

        assert room.db is None

    def test_team_names_are_carried_so_a_rival_reads_as_a_name(self) -> None:
        room = _resolve()

        assert room.team_names["t1"] == "Rivali"

    def test_the_roster_band_comes_through(self) -> None:
        assert _resolve().min_player == 30

    def test_the_admin_id_comes_through(self) -> None:
        assert _resolve().admin_id == "rival"

    def test_admin_id_absent_stays_none_not_a_fetch_failure(self) -> None:
        room = {k: v for k, v in BODY.items() if k != "admin_id"}
        assert _resolve(room).admin_id is None

    def test_seat_by_user_maps_every_held_seat(self) -> None:
        room = _resolve()

        assert room.seat_by_user == {"rival": "t1", "us": "t2"}

    def test_a_free_seat_has_no_uid_to_key_seat_by_user_on(self) -> None:
        assert "t3" not in _resolve().seat_by_user.values()


class TestWhatIsRefused:
    def test_a_room_that_is_not_mantra(self) -> None:
        with pytest.raises(RoomRefused, match="classic"):
            _resolve({**BODY, "asta_type": "classic"})

    def test_an_ordered_raise_mode(self) -> None:
        """The `raise_state` array an ordered room expects is undecoded (`docs/fantalab/06
        §8`), so our payload would be wrong. Refused rather than sent and hoped for."""
        with pytest.raises(RoomRefused, match="ordered"):
            _resolve({**BODY, "raise_mode": "ordered"})

    def test_being_unseated_names_the_free_seats(self) -> None:
        """The operator's next action is to claim one, so the message has to carry them."""
        with pytest.raises(RoomRefused, match="Libera"):
            _resolve(user_id="not-in-this-room")


class TestItCannotReachADatabase:
    def test_the_module_is_structurally_unable_to(self) -> None:
        import _importgraph

        assert not _importgraph.reaches(
            "fantabot.application.asta_room", "fantabot.adapters.persistence"
        )
