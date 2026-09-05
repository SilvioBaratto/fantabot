"""The room check — the only call that proves a stored FantaLab session authenticates.

Today the credential is captured by the Accounts page and read by nothing in the app, so
"is FantaLab connected?" has no answer beyond "a row exists". This endpoint asks the room
itself.

**It does not degrade open, and that is the point.** `todo/TODO.md` §3.4 records what
`except Exception -> found=False` costs: a database outage, a missing season, an
infeasible roster and a wrong `--format` all rendered as "No plan yet". Degrade-open is
right for a status read and wrong for the one call that tells the operator whether they
can bid tonight — so the five outcomes below each carry their own reason.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.room import check_room

OUR_UID = "uid-ours"
ROOM = "3f2a1b4c-1111-4222-8333-444455556666"
ROOM_URL = f"https://app.fantalab.it/asta?asta={ROOM}"


def _config(**over: Any) -> Any:
    """A `rest.RoomConfig` with a seat we hold, built through the real parser's type."""
    from fantabot.adapters.http.fantalab.rest import RoomConfig, Seat

    fields: dict[str, Any] = {
        "fantaleague_id": ROOM,
        "admin_id": "uid-admin",
        "db": 4,
        "asta_type": "mantra",
        "asta_mode": "chiamata",
        "raise_mode": "free",
        "num_teams": 8,
        "num_credits": 500,
        "min_player": None,
        "max_player": None,
        "number_of_players_selection": "min-max-goalie-others",
        "min_goalkeepers": 2,
        "max_goalkeepers": 4,
        "min_others": 23,
        "max_others": 28,
        "players_settings_data": None,
        "counter_time": 10,
        "counter_time_first": 20,
        "call_at_quotaz": False,
        "season": "2026/27",
        "is_live": True,
        "auction_running": True,
        "seats": [
            Seat(
                fantateam_id="team-ours",
                user_id=OUR_UID,
                position=1,
                team_name="Legamiallerotaie",
                max_credits=None,
            ),
            Seat(fantateam_id="team-free", user_id=None, position=2, team_name="Free", max_credits=None),
        ],
    }
    fields.update(over)
    return RoomConfig(**fields)


def _connect(
    *, user_id: str = OUR_UID, fetch: Callable[[str], Any] | None = None
) -> Callable[[], tuple[str, Callable[[str], Any]]]:
    """A `Connect` that hands back a uid and a fetch, opening nothing."""
    bound = fetch or (lambda _: _config())
    return lambda: (user_id, bound)


def _refuses() -> tuple[str, Callable[[str], Any]]:
    """A `Connect` that must never be called: the link did not parse.

    Parsing needs no credential, and the first live probe answered `not a link` with a
    decryption failure — a true statement about the credential and a useless answer to
    the question asked. This is the regression guard for that ordering.
    """
    raise AssertionError("the credential was opened before the link was parsed")


def test_a_room_we_hold_a_seat_in_resolves_with_every_field_the_cli_prints() -> None:
    check = check_room(ROOM_URL, connect=_connect())

    assert check.outcome == "resolved"
    assert check.fantaleague_id == ROOM
    assert check.shard == 4
    assert check.asta_mode == "chiamata"
    assert check.raise_mode == "free"
    assert check.num_teams == 8
    assert check.num_credits == 500
    assert check.seat_team_name == "Legamiallerotaie"
    # 2 keepers + 23 others is what the room declared, and the provenance says so rather
    # than leaving the reader to guess whether 25 was read or assumed.
    assert check.roster_size == 25
    assert check.roster_provenance == "read from the room"


def test_a_room_that_declares_nothing_says_the_band_was_assumed() -> None:
    check = check_room(
        ROOM_URL,
        connect=_connect(
            fetch=lambda _: _config(
                number_of_players_selection="no-limit-per-role",
                min_goalkeepers=None,
                min_others=None,
            )
        ),
    )

    assert check.outcome == "resolved"
    assert check.roster_provenance.startswith("assumed")


def test_a_seat_we_do_not_hold_is_refused_with_the_rooms_own_reason() -> None:
    check = check_room(ROOM_URL, connect=_connect(user_id="somebody-else"))

    assert check.outcome == "refused"
    assert "seat" in check.reason
    assert check.fantaleague_id == ROOM  # the link parsed; only the seat did not


def test_an_ordered_room_is_refused_rather_than_watched() -> None:
    check = check_room(ROOM_URL, connect=_connect(fetch=lambda _: _config(raise_mode="ordered")))

    assert check.outcome == "refused"
    assert "raise_mode" in check.reason


def test_an_invitation_link_keeps_the_message_that_says_what_to_paste() -> None:
    """What an admin actually sends. Refusing it as a generic parse error hands the
    operator a dead end holding the only link they had."""
    check = check_room("https://app.fantalab.it/join-asta?invitation_id=abc", connect=_refuses)

    assert check.outcome == "bad_link"
    assert "invitation" in check.reason


def test_a_string_that_is_not_a_room_link_is_its_own_outcome() -> None:
    check = check_room("not a link", connect=_refuses)

    assert check.outcome == "bad_link"
    assert check.fantaleague_id is None


def test_a_failing_fetch_is_unreachable_and_names_the_exception() -> None:
    """Not `refused`: the room said nothing, we could not ask it."""

    def boom(_: str) -> Any:
        raise RuntimeError("401 Unauthorized")

    check = check_room(ROOM_URL, connect=_connect(fetch=boom))

    assert check.outcome == "unreachable"
    assert "RuntimeError" in check.reason
    assert "401" in check.reason


def test_nothing_stored_is_no_credential_and_names_the_remedy() -> None:
    """A missing credential is a remedy, not a failure — and it names the remedy."""
    from fantabot.domain.tokens.errors import FantalabSessionMissing

    def missing() -> tuple[str, Callable[[str], Any]]:
        raise FantalabSessionMissing()

    check = check_room(ROOM_URL, connect=missing)

    assert check.outcome == "no_credential"
    assert "fantalab-login" in check.reason or "Accounts" in check.reason


def test_a_row_written_under_another_key_is_no_credential_not_no_session() -> None:
    """Found by the first live probe against the bundled database.

    The stored FantaLab row was written under key `aa695c77` and `.env` now holds
    `ef341176` — `todo/TODO.md` §1.2 excluded credentials from that migration on purpose.
    Reporting it as "no session" would send the operator to reconnect without telling
    them their key had changed under it, so both share an outcome and keep their own
    words.
    """
    from fantabot.domain.tokens.errors import TokenUndecryptable

    def wrong_key() -> tuple[str, Callable[[str], Any]]:
        raise TokenUndecryptable("this row was encrypted with key aa695c77")

    check = check_room(ROOM_URL, connect=wrong_key)

    assert check.outcome == "no_credential"
    assert "aa695c77" in check.reason


def test_the_endpoint_answers_rather_than_500ing_when_the_database_is_gone(
    monkeypatch: Any,
) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom() -> Any:
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/asta/room", params={"url": ROOM_URL})

    assert response.status_code == 200
    assert response.json()["outcome"] == "unreachable"


@pytest.mark.parametrize(
    "outcome", ["resolved", "refused", "bad_link", "no_credential", "unreachable"]
)
def test_every_outcome_is_a_distinct_name(outcome: str) -> None:
    """Pinned as a list because §3.4's defect was four failures wearing one label."""
    from fantabot_app.api.v1.endpoints.room import OUTCOMES

    assert outcome in OUTCOMES
