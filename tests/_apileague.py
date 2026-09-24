"""The `MockTransport` scaffolding both `apileague` suites are built on.

Copy-pasted into `tests/adapters/http/test_apileague_client.py` and
`test_apileague_teamlineup.py` until 2026-09-24. `Result`, `FakeSession` and `Recorder`
were byte-identical in both; `a_store` and the transport factory differed only in their
defaults -- and the teamlineup copy had **dropped `a_store`'s `expires_at` keyword**, so
that suite had no way to express an expired token. The keyword lives here now, once, so
the drift cannot reopen.

⚠ The teamlineup suite still has no expired-token test, and deliberately: expiry is
refused in `apileague._send` -> `auth_headers`, which the read and the submit share, and
`test_apileague_client.py`'s `TokenExpired` case already covers that one site. Adding the
case to the second suite would pin the same line twice.

One behavioural difference is folded in rather than carried over: the client's factory
read `json_body or STATUS_BODY`, so `transport_returning(401, {})` served the **status**
body instead of the empty one it was handed. No test depended on it (an empty body and a
body with no `code` both reach the unlabelled-401 branch, and a >=400 status raises before
any parse), and the `is None` test here is what the teamlineup copy already used.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
from cryptography.fernet import Fernet

from fantabot.adapters.persistence.models.tokens import LeagueToken
from fantabot.adapters.tokens.store import TokenStore
from fantabot.domain.tokens.crypto import TokenCipher


class Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def all(self) -> Any:
        return self._value if isinstance(self._value, list) else []


class FakeSession:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)

    def execute(self, statement: Any, params: Any = None) -> Result:
        return Result(self.answers.pop(0) if self.answers else None)


class Recorder:
    """A `MockTransport` handler that records the request it was given."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def build_store(
    now: datetime,
    plaintext: str,
    *,
    league_id: int,
    user_id: str,
    team_id: int,
    expires_at: datetime | None = None,
    row: bool = True,
) -> TokenStore:
    """A `TokenStore` over one synthetic row, under a key generated per call.

    `expires_at` defaults to a year out. It is a parameter and not a constant because a
    store whose row has already expired is how the refusal-before-any-request tests are
    written, and the suite that lost the keyword lost the ability to write one.
    """
    cipher = TokenCipher(Fernet.generate_key().decode())
    stored = (
        LeagueToken(
            league_id=league_id,
            ciphertext=cipher.encrypt(plaintext),
            key_fingerprint=cipher.fingerprint,
            issued_at=now - timedelta(days=7),
            expires_at=expires_at or now + timedelta(days=357),
            user_id=user_id,
            team_id=team_id,
            league_name="Legamiallerotaie2",
            captured_at=now,
            last_seen_at=now,
            last_verified_at=None,
        )
        if row
        else None
    )
    return TokenStore(FakeSession(stored), cipher)


def mock_transport(
    status: int, body: dict[str, Any]
) -> tuple[httpx.MockTransport, Recorder]:
    """`(transport, handler)` — the handler is how a test reads the request back."""
    handler = Recorder(httpx.Response(status, json=body))
    return httpx.MockTransport(handler), handler
