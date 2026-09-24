"""The room check — the only call that proves a stored FantaLab session authenticates.

Today the credential is captured by the Accounts page and read by nothing in the app, so
"is FantaLab connected?" has no answer beyond "a row exists". This endpoint asks the room
itself.

**It does not degrade open, and that is the point.** T31 (`tasks/BACKLOG.md`) records what
`except Exception -> found=False` costs: a database outage, a missing season, an
infeasible roster and a wrong `--format` all rendered as "No plan yet". Degrade-open is
right for a status read and wrong for the one call that tells the operator whether they
can bid tonight — so the five outcomes below each carry their own reason.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.room import check_room

from .test_outcomes import outcomes_returned

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


def test_the_resolved_room_carries_the_uid_a_bid_would_be_signed_with() -> None:
    """`POST /asta/room/bid` reads it from here rather than resolving the room again.

    Survivor 12 of 3.9b's battery: dropping `seat_user_id` from this response left every
    test green, because the bid route's own suite fakes `check_room` whole — so the field
    that signs a raise was only ever asserted against a stand-in. A second resolution path
    to learn one uid is a second set of outcomes, and they drift.

    Asserted beside the seat on purpose: the pair is what a bid payload carries, and swapping
    them is a `200` that drives somebody else's team all evening.
    """
    check = check_room(ROOM_URL, connect=_connect())

    assert check.seat_user_id == OUR_UID
    assert check.seat_team_id != check.seat_user_id, (
        "the fixture's seat and uid are the same string, so this could not tell them apart"
    )


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
    """Not `refused`: the room said nothing, we could not ask it.

    **The exception is a real one now.** This raised a bare `RuntimeError("401
    Unauthorized")` until 2026-09-24 — a stand-in that passed only because the handler
    under it was `except Exception`, so the test could not tell a named family from an
    unnamed one and quietly asserted that the route swallowed *anything*. A `401` from
    this host arrives as `httpx.HTTPStatusError` out of `fetch_league`'s
    `raise_for_status()`, which is what is raised here.
    """

    def boom(_: str) -> Any:
        raise httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("POST", "https://api.fantalab.it/fantaleague/fetch"),
            response=httpx.Response(401),
        )

    check = check_room(ROOM_URL, connect=_connect(fetch=boom))

    assert check.outcome == "unreachable"
    assert "HTTPStatusError" in check.reason
    assert "401" in check.reason


def test_a_failure_with_nothing_to_say_is_named_rather_than_a_500() -> None:
    """`str(TimeoutError())` is `""` and `"".splitlines()` is `[]`.

    The helper that builds this `reason` raised `IndexError` on that, inside the very
    `except` clause that calls it — so a room check against a room that simply stopped
    answering returned a 500 instead of the outcome this module exists to name. The type
    alone is the reason here: a colon with nothing after it promises a sentence there is
    not.
    """

    def boom(_: str) -> Any:
        raise TimeoutError

    check = check_room(ROOM_URL, connect=_connect(fetch=boom))

    assert check.outcome == "unreachable"
    assert check.reason == "TimeoutError"
    assert check.fantaleague_id == ROOM  # the link parsed; only the room did not answer


def test_a_message_less_connect_failure_is_named_too() -> None:
    """The other call site, and the one `test_the_endpoint_answers_rather_than_500ing...`
    below covers only with a talkative exception. A database handle that will not open
    raises `OSError` with nothing to say far more often than with a sentence."""

    def no_database() -> tuple[str, Callable[[str], Any]]:
        raise OSError

    check = check_room(ROOM_URL, connect=no_database)

    assert check.outcome == "unreachable"
    assert check.reason == "OSError"


def test_the_room_route_holds_no_second_copy_of_the_one_line_reason() -> None:
    """One sentence, one implementation.

    `room.py` carried its own `_because` until 2026-09-24, identical to
    `api/outcomes.because` down to the unguarded `[0]` — so the bug had two homes and
    fixing either would have left the other. Pinned by identity rather than by behaviour
    because a re-added copy would pass every behavioural test in this file on the day it
    was written, and drift afterwards.
    """
    from fantabot_app.api.outcomes import because
    from fantabot_app.api.v1.endpoints import room

    assert not hasattr(room, "_because"), "the second copy is back"
    assert getattr(room, "because", None) is because


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
    `ef341176` — the two-databases migration excluded credentials on purpose.
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
    """A driver error, not a `RuntimeError` — for the reason above, and one more.

    `stored_connect` opens a session; a database that will not open raises out of the
    driver, and `SQLAlchemyError` is the family `room.py` names for it. The old
    `RuntimeError` proved nothing about the route beyond that it caught everything.
    """
    from fantabot.adapters.persistence import database_manager
    from sqlalchemy.exc import OperationalError

    def boom() -> Any:
        raise OperationalError("SELECT 1", {}, OSError("db unreachable"))

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/asta/room", params={"url": ROOM_URL})

    assert response.status_code == 200
    assert response.json()["outcome"] == "unreachable"
    assert "OperationalError" in response.json()["reason"]


def test_a_failure_outside_the_named_families_reaches_fastapi_as_a_500(
    monkeypatch: Any,
) -> None:
    """The other half of `api/outcomes.py`'s rule, and the half nothing asserted.

    "Fail closed on a decision" is not only that each named failure gets its own screen —
    it is that an **un**named one is loud. A bare handler turns an unanticipated bug into
    a tidy page saying we could not ask, which is a true-looking sentence about a fault
    nobody will now investigate. `room.py` caught bare `Exception` twice until 2026-09-24;
    without this test, putting either one back would turn nothing red.

    A `RuntimeError` out of the session factory is a bug in this repository, so it is a
    500 with a traceback in the log rather than an `unreachable` the operator reads as an
    outage.
    """
    from fantabot.adapters.persistence import database_manager

    def boom() -> Any:
        raise RuntimeError("a bug, not an outage")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/asta/room", params={"url": ROOM_URL}
    )

    assert response.status_code == 500


def test_the_route_returns_exactly_the_outcomes_it_pins() -> None:
    """`OUTCOMES` and what `check_room` actually builds, compared for equality.

    This was five hand-typed literals and `assert outcome in OUTCOMES` — a subset check,
    in the direction that cannot fail. Both halves of it were latent:

    * a **sixth** name in `OUTCOMES` that no branch of `check_room` returns passed for
      ever, and the frontend would carry a branch for a screen the route cannot render;
    * a branch **deleted** from the route passed too, as long as its name stayed in the
      tuple — and the hand-typed list, being a third copy, could drift from both.

    The two sets were measured as agreeing on 2026-09-24, so nothing was wrong; the test
    was simply incapable of saying so. It now asks `test_outcomes.py`'s own scan — the
    same function that holds `asta.py`, `lineup.py` and `pricing.py` to this, rather than
    a second copy of it here that could answer differently.
    """
    from fantabot_app.api.v1.endpoints.room import OUTCOMES

    scan = outcomes_returned("room.py", "RoomCheck")

    assert not scan.unreadable, (
        f"the scan could not read {scan.unreadable} — an outcome it cannot read is a "
        "screen it cannot police."
    )
    assert scan.named, "room.py names no outcome at all — this scan reads nothing"
    assert scan.named == set(OUTCOMES), (
        f"check_room returns {sorted(scan.named)} and room.py pins {sorted(OUTCOMES)}. A "
        "name in the tuple that no branch returns is a screen the frontend has a branch "
        "for and the route cannot reach; a branch whose name is gone from the tuple is "
        "the opposite."
    )
    assert len(set(OUTCOMES)) == len(OUTCOMES), (
        f"{OUTCOMES} repeats a name — §3.4's defect was four failures wearing one label, "
        "and a tuple with a duplicate in it is that defect written down."
    )
