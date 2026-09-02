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
from datetime import UTC, datetime, timedelta
from typing import Any

import _tokens
import httpx
import pytest
from cryptography.fernet import Fernet

from fantabot.adapters.http import apileague
from fantabot.adapters.persistence.models.tokens import LeagueToken
from fantabot.adapters.tokens.store import TokenStore
from fantabot.domain.lineup.errors import LineupRejected
from fantabot.domain.tokens.crypto import TokenCipher
from fantabot.domain.tokens.errors import TokenMissing, TokenRejected

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


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def all(self) -> Any:
        return self._value if isinstance(self._value, list) else []


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)

    def execute(self, statement: Any, params: Any = None) -> _Result:
        return _Result(self.answers.pop(0) if self.answers else None)


def a_store(*, row: bool = True) -> TokenStore:
    cipher = TokenCipher(Fernet.generate_key().decode())
    stored = (
        LeagueToken(
            league_id=_tokens.LEGA_MANTRA,
            ciphertext=cipher.encrypt(PLAINTEXT),
            key_fingerprint=cipher.fingerprint,
            issued_at=NOW - timedelta(days=7),
            expires_at=NOW + timedelta(days=357),
            user_id=_tokens.USER_ID,
            team_id=_tokens.TEAM_MANTRA,
            league_name="Legamiallerotaie2",
            captured_at=NOW,
            last_seen_at=NOW,
            last_verified_at=None,
        )
        if row
        else None
    )
    return TokenStore(_Session(stored), cipher)


class _Recorder:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def transport_returning(
    status: int = 200, json_body: dict[str, Any] | None = None
) -> tuple[httpx.MockTransport, _Recorder]:
    body = DTO_BODY if json_body is None else json_body
    handler = _Recorder(httpx.Response(status, json=body))
    return httpx.MockTransport(handler), handler


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
