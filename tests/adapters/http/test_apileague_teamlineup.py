"""The `gaming/v1/teamLineup` read + submit, on `httpx.MockTransport`. **Zero sockets.**

The lineup endpoints live under a different microservice (`gaming/v1`) than the rest of
`apileague` (`onboarding/v1`), captured live 2026-09-02 — see `docs/leghe-api.md`. Read is
a plain `GET`; submit is a `POST` whose body is the formation. The two share `auth_headers`
and the leak guard with the `onboarding` endpoints, so what is actually new here is the
POST body, the `gaming/v1` paths, and the `LUP009` rejection the platform returns when the
formation is not fieldable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import _tokens
import httpx
import pytest
from _apileague import Recorder as _Recorder
from _apileague import build_store, mock_transport

from fantabot.adapters.http import apileague
from fantabot.adapters.tokens.store import TokenStore
from fantabot.domain.lineup.errors import LineupRejected
from fantabot.domain.tokens.errors import ApiUnavailable, TokenMissing, TokenRejected

NOW = datetime(2026, 9, 2, tzinfo=UTC)
PLAINTEXT = _tokens.make_token(l_id=_tokens.LEGA_MANTRA, t_id=_tokens.TEAM_MANTRA)
COMPETITION = 311681

DTO_BODY = {
    "teamLineupDto": {"mdl": "343", "starts": [6482, 2788], "bench": [4360], "ldate": "x"},
    "lineUpInfo": [],
}
PAYLOAD = {
    "starts": [6482, 2788, 7564, 7274, 7181, 1850, 5504, 5678, 2194, 6875, 4179],
    "bench": [4360, 5750, 4137, 4998, 5620, 5680, 4459, 6898, 7198, 4947, 5319, 7126],
    "capt": [],
    "mdl": "343",
    "idcomp": COMPETITION,
    "mday": 1,
    "cmday": 3,
    "tid": _tokens.TEAM_MANTRA,
    "allComp": False,
    "visb": True,
    "swtcA": 0,
    "swtcB": 0,
    "swtc": 0,
    "swtcMdl": "343",
}


def a_store(*, expires_at: datetime | None = None, row: bool = True) -> TokenStore:
    """This suite's store, over `_apileague.build_store`'s one synthetic row.

    `expires_at` is here because the copy this replaced had dropped it, which is what left
    the submit suite unable to say "the stored token has expired". Nothing here passes it
    yet, and nothing should: the refusal lives in `apileague._send` -> `auth_headers`,
    shared with every read, and `test_apileague_client.py` already pins it there.
    """
    return build_store(
        NOW, PLAINTEXT,
        league_id=_tokens.LEGA_MANTRA, user_id=_tokens.USER_ID, team_id=_tokens.TEAM_MANTRA,
        expires_at=expires_at, row=row,
    )


def transport_returning(
    status: int = 200, json_body: dict[str, Any] | None = None
) -> tuple[httpx.MockTransport, _Recorder]:
    return mock_transport(status, DTO_BODY if json_body is None else json_body)


# --- one match's detail: the per-player scores, once a round is calculated ------------


MATCH_BODY = {"cal": True, "mday": 1, "cmday": 3, "home": {"tot": 82.0}, "away": {"tot": 73.0}}


def test_match_detail_requests_the_match_path_with_both_teams() -> None:
    transport, handler = transport_returning(json_body=MATCH_BODY)

    apileague.match_detail(
        _tokens.LEGA_MANTRA, COMPETITION, mday=1, cmday=3, home=18774379, away=19131109,
        store=a_store(), transport=transport,
    )

    request = handler.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/gaming/v1/teamLineup/{COMPETITION}/1/3/18774379/19131109"


def test_match_detail_returns_the_body_whole() -> None:
    transport, _ = transport_returning(json_body=MATCH_BODY)

    body = apileague.match_detail(
        _tokens.LEGA_MANTRA, COMPETITION, mday=1, cmday=3, home=18774379, away=19131109,
        store=a_store(), transport=transport,
    )

    assert body == MATCH_BODY


# --- read -----------------------------------------------------------------


def test_read_requests_the_gaming_visualizza_path() -> None:
    transport, handler = transport_returning()

    apileague.teamLineup_read(
        _tokens.LEGA_MANTRA, COMPETITION, store=a_store(), transport=transport
    )

    request = handler.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/gaming/v1/teamLineup/visualizza/A/{COMPETITION}"


def test_read_returns_the_parsed_body() -> None:
    transport, _ = transport_returning()

    body = apileague.teamLineup_read(
        _tokens.LEGA_MANTRA, COMPETITION, store=a_store(), transport=transport
    )

    assert body["teamLineupDto"]["mdl"] == "343"


def test_read_carries_the_two_documented_headers() -> None:
    transport, handler = transport_returning()

    apileague.teamLineup_read(
        _tokens.LEGA_MANTRA, COMPETITION, store=a_store(), transport=transport
    )

    request = handler.requests[0]
    assert request.headers["app_key"] == apileague.APP_KEY
    assert request.headers["Authorization"] == f"Bearer {PLAINTEXT}"


# --- submit ---------------------------------------------------------------


def test_submit_posts_to_the_division_path_with_the_body() -> None:
    transport, handler = transport_returning(json_body=DTO_BODY["teamLineupDto"])

    apileague.teamLineup_submit(
        _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
    )

    request = handler.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/gaming/v1/teamLineup/A"
    assert json.loads(request.content) == PAYLOAD


def test_submit_sends_json_content_type_alongside_the_auth_headers() -> None:
    transport, handler = transport_returning(json_body=DTO_BODY["teamLineupDto"])

    apileague.teamLineup_submit(
        _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
    )

    request = handler.requests[0]
    assert request.headers["app_key"] == apileague.APP_KEY
    assert request.headers["Authorization"] == f"Bearer {PLAINTEXT}"
    assert request.headers["Content-Type"].startswith("application/json")


def test_submit_returns_the_parsed_body() -> None:
    transport, _ = transport_returning(json_body={"mdl": "343", "ldate": "20260902"})

    body = apileague.teamLineup_submit(
        _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
    )

    assert body["mdl"] == "343"


def test_a_lup009_rejection_becomes_a_named_lineup_error() -> None:
    transport, _ = transport_returning(
        400, {"code": "LUP009", "message": "The formation module is not allowed."}
    )

    with pytest.raises(LineupRejected) as caught:
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
        )

    assert "LUP009" in str(caught.value)


def test_a_lup009_rejection_carries_the_platform_message() -> None:
    """The code says *that* the platform refused; only its message says *what* it read, and
    the next refusal is diagnosed offline from the run record or not at all."""
    transport, _ = transport_returning(
        400, {"code": "LUP009", "message": "The formation module is not allowed."}
    )

    with pytest.raises(LineupRejected) as caught:
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
        )

    assert caught.value.code == "LUP009"
    assert caught.value.message == "The formation module is not allowed."
    assert "The formation module is not allowed." in str(caught.value)


@pytest.mark.parametrize("body", [{"code": "LUP009"}, {"code": "LUP009", "message": None}])
def test_a_rejection_without_a_string_message_carries_an_empty_one(
    body: dict[str, object],
) -> None:
    transport, _ = transport_returning(400, body)

    with pytest.raises(LineupRejected) as caught:
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
        )

    assert caught.value.message == ""


def test_a_non_json_body_becomes_a_named_error_not_a_parse_traceback() -> None:
    """A 200 with a non-JSON body (e.g. an intercepting proxy's HTML) must not let
    `response.json()`'s ValueError escape with the token live on the frame."""
    handler = _Recorder(httpx.Response(200, headers={"content-type": "text/html"}, text="<html>"))
    transport = httpx.MockTransport(handler)

    with pytest.raises(ApiUnavailable) as caught:
        apileague.teamLineup_read(_tokens.LEGA_MANTRA, COMPETITION, store=a_store(), transport=transport)

    assert "httpx" not in str(caught.value)


def test_a_decoding_error_is_caught_by_the_leak_guard() -> None:
    """`httpx.DecodingError` is a RequestError but not a TransportError — it must still be
    mapped, never re-raised (its `.request` carries the Authorization header)."""
    transport = httpx.MockTransport(_Recorder(httpx.DecodingError("bad encoding")))

    with pytest.raises(ApiUnavailable):
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
        )


def test_submit_shares_the_401_token_mapping() -> None:
    transport, _ = transport_returning(401, {"code": "ATH001"})

    with pytest.raises(TokenRejected):
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
        )


def test_submit_with_no_token_is_refused_before_a_request_is_built() -> None:
    transport, handler = transport_returning()

    with pytest.raises(TokenMissing):
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(row=False), transport=transport
        )

    assert handler.requests == [], "a request was built for a token we do not have"


# --- competitions (array) + lineup settings -------------------------------


def test_competitions_returns_the_array_from_the_documented_path() -> None:
    comps = [{"id": 311681, "tmids": [1, 2], "del": False}]
    transport, handler = transport_returning(json_body=comps)

    result = apileague.competitions(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    assert handler.requests[0].url.path == "/onboarding/v1/league/competitions"
    assert result == comps


def test_a_non_array_competitions_body_yields_an_empty_list() -> None:
    transport, _ = transport_returning(json_body={"unexpected": "dict"})

    assert apileague.competitions(_tokens.LEGA_MANTRA, store=a_store(), transport=transport) == []


def test_lineup_settings_reads_the_settings_path() -> None:
    body = {"mods": ["343", "442"], "tbench": 12}
    transport, handler = transport_returning(json_body=body)

    result = apileague.lineup_settings(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    assert handler.requests[0].url.path == "/onboarding/v1/league/settings/lineup"
    assert result["mods"] == ["343", "442"]


# --- nothing leaks --------------------------------------------------------


def test_a_lineup_rejection_does_not_leak_the_token() -> None:
    transport, _ = transport_returning(400, {"code": "LUP009", "message": "nope"})

    try:
        apileague.teamLineup_submit(
            _tokens.LEGA_MANTRA, PAYLOAD, store=a_store(), transport=transport
        )
    except LineupRejected as exc:
        message = str(exc)
    else:  # pragma: no cover - the call above always raises
        raise AssertionError("LUP009 did not raise")

    leaked = [
        PLAINTEXT[i : i + 8]
        for i in range(len(PLAINTEXT) - 7)
        if PLAINTEXT[i : i + 8] in message
    ]
    assert leaked == []
