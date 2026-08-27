"""Reading a FantaLab session out of a browser's localStorage. Pure.

Measured on 2026-08-27 (spike S3): the credential lives in `localStorage`, not
in a cookie and not only in IndexedDB. `refresh_token` is the durable one — the
app derives a fresh `id_token` on every page load via /sign-in, so the ~1 h
expiry is not the constraint it looks like.

No real credential appears in this file. The shapes are synthesized, as
`tests/_tokens.py` does for the other service.
"""

from __future__ import annotations

import re

import pytest

from fantabot.tokens.errors import TokenError
from fantabot.tokens.fantalab import FantalabSession, parse_fantalab_storage

STORAGE = {
    "origins": [
        {
            "origin": "https://app.fantalab.it",
            "localStorage": [
                {"name": "refresh_token", "value": "refresh-xyz"},
                {"name": "id_token", "value": "id-xyz"},
                {"name": "access_token", "value": "access-xyz"},
                {"name": "user_id", "value": "fee799b2-1351-4695-b1f8-79d6ace8a4e6"},
                {"name": "user_email", "value": "someone@example.com"},
                {"name": "homeMode", "value": "mantra"},
            ],
        }
    ]
}


def test_the_durable_credential_is_captured() -> None:
    session = parse_fantalab_storage(STORAGE)
    assert session.user_id == "fee799b2-1351-4695-b1f8-79d6ace8a4e6"
    assert session.refresh_token == "refresh-xyz"


def test_the_email_is_not_captured() -> None:
    """It is in localStorage and it is not needed. Storing personal data we have
    no use for is a cost with no benefit."""
    session = parse_fantalab_storage(STORAGE)
    assert "someone@example.com" not in session.as_blob()


def test_a_session_without_a_refresh_token_is_refused() -> None:
    """The id_token expires in about an hour. Storing one and calling it a
    session would produce a login that works until you next need it."""
    storage = {
        "origins": [
            {
                "origin": "https://app.fantalab.it",
                "localStorage": [
                    {"name": "id_token", "value": "id-xyz"},
                    {"name": "user_id", "value": "u"},
                ],
            }
        ]
    }
    with pytest.raises(TokenError, match="refresh_token"):
        parse_fantalab_storage(storage)


def test_another_origin_is_ignored() -> None:
    """A shared browser profile carries other sites' storage. Reading a token
    from one of them would be both wrong and a privacy breach."""
    storage = {
        "origins": [
            {
                "origin": "https://example.com",
                "localStorage": [{"name": "refresh_token", "value": "not-ours"}],
            },
            STORAGE["origins"][0],
        ]
    }
    assert parse_fantalab_storage(storage).refresh_token == "refresh-xyz"


def test_an_empty_storage_is_refused_with_an_instruction() -> None:
    with pytest.raises(TokenError, match=re.escape("app.fantalab.it")):
        parse_fantalab_storage({"origins": []})


def test_the_session_never_shows_its_credential() -> None:
    """`repr` reaches logs, tracebacks and debuggers. CLAUDE.md: a bearer token
    is never printed, logged or `repr`'d — in any form, truncated or whole."""
    session = FantalabSession(user_id="u", refresh_token="secret-value",
                              id_token="also-secret", access_token="secret-too")
    rendered = repr(session)
    assert "secret" not in rendered
    assert "u" in rendered


def test_a_blob_round_trips() -> None:
    """One ciphertext holds the whole session, so a partial write cannot leave
    two of three tokens stored."""
    session = parse_fantalab_storage(STORAGE)
    assert FantalabSession.from_blob(session.user_id, session.as_blob()) == session
