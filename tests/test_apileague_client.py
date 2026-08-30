"""The apileague client, on `httpx.MockTransport`. **Zero sockets.**

That is the literal form of SC 13's "enforced by a test that fails if a socket is
opened": every test here runs in the default tier under `conftest`'s autouse
guard, which raises on `socket.connect`. If the client ever built a real
transport, these tests would fail rather than quietly reaching the network.

The refusals are the interesting half. A missing or expired token must be
refused *before* a request exists, and the mock handler never being called is
how that is proved — an assertion on the exception type alone would pass even if
the socket had been opened first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import _tokens
import httpx
import pytest
from cryptography.fernet import Fernet

from fantabot.adapters.http import apileague as apileague
from fantabot.adapters.persistence.models.tokens import LeagueToken
from fantabot.adapters.tokens.store import TokenStore
from fantabot.domain.tokens.crypto import TokenCipher
from fantabot.domain.tokens.errors import (
    ApiTimeout,
    ApiUnavailable,
    AppKeyRejected,
    TokenExpired,
    TokenMissing,
    TokenRejected,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)
PLAINTEXT = _tokens.make_token(l_id=_tokens.LEGA_MANTRA, t_id=_tokens.TEAM_MANTRA)
STATUS_BODY = {"sto": False, "activ": True, "sId": 21, "mday": 2, "mstr": "2026-08-28T18:45:00"}


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


def a_store(*, expires_at: datetime | None = None, row: bool = True) -> TokenStore:
    cipher = TokenCipher(Fernet.generate_key().decode())
    stored = (
        LeagueToken(
            league_id=_tokens.LEGA_MANTRA,
            ciphertext=cipher.encrypt(PLAINTEXT),
            key_fingerprint=cipher.fingerprint,
            issued_at=NOW - timedelta(days=7),
            expires_at=expires_at or NOW + timedelta(days=357),
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
    """A MockTransport handler that records the request it was given."""

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
    handler = _Recorder(httpx.Response(status, json=json_body or STATUS_BODY))
    return httpx.MockTransport(handler), handler


# --- the headers ----------------------------------------------------------


def test_the_request_carries_exactly_the_two_documented_headers() -> None:
    transport, handler = transport_returning()

    apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    request = handler.requests[0]
    assert request.headers["app_key"] == apileague.APP_KEY
    assert request.headers["Authorization"] == f"Bearer {PLAINTEXT}"


def test_the_base_url_comes_from_settings() -> None:
    transport, handler = transport_returning()

    apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    from fantabot.config import settings

    assert str(handler.requests[0].url).startswith(settings.fantabot_apileague_base_url)
    assert handler.requests[0].url.path == apileague.LEAGUE_STATUS_PATH


def test_a_successful_call_returns_the_parsed_body() -> None:
    transport, _ = transport_returning()

    body = apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    assert body["sId"] == 21
    assert body["mday"] == 2


def test_auth_headers_alone_builds_no_request() -> None:
    headers = apileague.auth_headers(_tokens.LEGA_MANTRA, store=a_store())

    assert set(headers) == {"app_key", "Authorization"}


# --- SC 13: refused before a socket exists --------------------------------


def test_a_missing_token_is_refused_without_the_transport_being_touched() -> None:
    """The handler never being called is the proof. An assertion on the
    exception type alone would pass even if a socket had been opened first."""
    transport, handler = transport_returning()

    with pytest.raises(TokenMissing):
        apileague.league_status(
            _tokens.LEGA_MANTRA, store=a_store(row=False), transport=transport
        )

    assert handler.requests == [], "a request was built for a token we do not have"


def test_an_expired_token_is_refused_without_the_transport_being_touched() -> None:
    transport, handler = transport_returning()

    with pytest.raises(TokenExpired):
        apileague.league_status(
            _tokens.LEGA_MANTRA,
            store=a_store(expires_at=NOW - timedelta(days=1)),
            transport=transport,
            now=NOW,
        )

    assert handler.requests == []


# --- SC 14: the two 401s mean different things ----------------------------


def test_ath001_says_the_token_was_rejected_and_names_the_fix() -> None:
    transport, _ = transport_returning(
        401, {"code": "ATH001", "message": "Not authorized to access the services"}
    )

    with pytest.raises(TokenRejected) as caught:
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    message = str(caught.value)
    assert "fantabot auth login" in message
    assert str(_tokens.LEGA_MANTRA) in message


def test_ath007_points_at_the_bundle_regrep_not_at_a_missing_header() -> None:
    """The doc records ATH007 as "Application key is missing", observed when no
    header was sent. fantabot always sends one, so "missing" would send the next
    reader looking for a bug that is not there. It rotated."""
    transport, _ = transport_returning(401, {"code": "ATH007", "message": "…"})

    with pytest.raises(AppKeyRejected) as caught:
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    message = str(caught.value)
    assert "rotated" in message
    assert "docs/leghe-api.md" in message


def test_an_unlabelled_401_is_still_a_rejected_token() -> None:
    transport, _ = transport_returning(401, {})

    with pytest.raises(TokenRejected):
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)


def test_a_500_says_the_stored_token_is_untouched() -> None:
    """A server fault must not read as a lost credential."""
    transport, _ = transport_returning(500, {})

    with pytest.raises(ApiUnavailable) as caught:
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    assert "untouched" in str(caught.value)


# --- transport failures ---------------------------------------------------


def test_a_timeout_becomes_a_named_error_not_an_httpx_exception() -> None:
    transport = httpx.MockTransport(_Recorder(httpx.TimeoutException("timed out")))

    with pytest.raises(ApiTimeout) as caught:
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    assert "untouched" in str(caught.value)
    assert "--no-verify" in str(caught.value)


def test_a_connect_failure_becomes_a_named_error() -> None:
    transport = httpx.MockTransport(_Recorder(httpx.ConnectError("no route")))

    with pytest.raises(ApiUnavailable):
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)


def test_no_httpx_exception_chains_into_the_traceback() -> None:
    """`httpx.RequestError` carries `.request`, and a rendered traceback can
    surface the Authorization header."""
    transport = httpx.MockTransport(_Recorder(httpx.TimeoutException("timed out")))

    with pytest.raises(ApiTimeout) as caught:
        apileague.league_status(_tokens.LEGA_MANTRA, store=a_store(), transport=transport)

    assert caught.value.__cause__ is None


# --- nothing leaks --------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["ath001", "ath007", "server", "timeout", "connect"],
)
def test_no_error_message_contains_the_token(case: str) -> None:
    transports = {
        "ath001": transport_returning(401, {"code": "ATH001"})[0],
        "ath007": transport_returning(401, {"code": "ATH007"})[0],
        "server": transport_returning(503, {})[0],
        "timeout": httpx.MockTransport(_Recorder(httpx.TimeoutException("x"))),
        "connect": httpx.MockTransport(_Recorder(httpx.ConnectError("x"))),
    }

    try:
        apileague.league_status(
            _tokens.LEGA_MANTRA, store=a_store(), transport=transports[case]
        )
    except Exception as exc:
        message = str(exc)
    else:  # pragma: no cover - every case above raises
        raise AssertionError(f"{case} did not raise")

    leaked = [
        PLAINTEXT[i : i + 8]
        for i in range(len(PLAINTEXT) - 7)
        if PLAINTEXT[i : i + 8] in message
    ]
    assert leaked == [], f"{case} leaked {leaked}"
    assert "httpx" not in message
