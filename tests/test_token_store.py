"""`TokenStore` — the single decryption site, exercised without a database.

A real `TokenCipher` over a throwaway key, and the `_FakeSession` pattern from
`tests/test_repositories_fake.py`. Real crypto, fake SQL: the encryption is the
part worth exercising for real, and the SQL is already pinned there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import _tokens
import pytest
from cryptography.fernet import Fernet

from fantabot.db.models.tokens import LeagueToken
from fantabot.tokens.capture import CapturedToken
from fantabot.tokens.claims import decode_claims
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.errors import (
    KeyMissing,
    TokenExpired,
    TokenMissing,
    TokenUndecryptable,
)
from fantabot.tokens.status import TokenStatus
from fantabot.tokens.store import TokenStore

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def a_cipher() -> TokenCipher:
    return TokenCipher(Fernet.generate_key().decode())


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def all(self) -> Any:
        return self._value if isinstance(self._value, list) else []

    @property
    def rowcount(self) -> int:
        return 1 if self._value else 0


class _Session:
    """Answers with a queue; records what it was asked."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []

    def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement).split("\n")[0])
        return _Result(self.answers.pop(0) if self.answers else None)


def a_stored_row(
    cipher: TokenCipher,
    *,
    plaintext: str | None = None,
    expires_at: datetime | None = None,
    fingerprint: str | None = None,
) -> LeagueToken:
    token = plaintext or _tokens.make_token(l_id=_tokens.LEGA_MANTRA)
    return LeagueToken(
        league_id=_tokens.LEGA_MANTRA,
        ciphertext=cipher.encrypt(token),
        key_fingerprint=fingerprint or cipher.fingerprint,
        issued_at=NOW - timedelta(days=7),
        expires_at=expires_at or NOW + timedelta(days=357),
        user_id=_tokens.USER_ID,
        team_id=_tokens.TEAM_MANTRA,
        league_name="Legamiallerotaie2",
        captured_at=NOW,
        last_seen_at=NOW,
        last_verified_at=None,
    )


# --- the round trip -------------------------------------------------------


def test_a_stored_token_comes_back_byte_identical() -> None:
    cipher = a_cipher()
    plaintext = _tokens.make_token(l_id=_tokens.LEGA_MANTRA)
    session = _Session(a_stored_row(cipher, plaintext=plaintext))

    assert TokenStore(session, cipher).load_plaintext(_tokens.LEGA_MANTRA) == plaintext


def test_saving_two_leghe_upserts_both_and_stamps_them_once() -> None:
    cipher = a_cipher()
    session = _Session()
    captured = [
        CapturedToken(
            league_id=lid,
            league_name=str(lid),
            token=_tokens.make_token(l_id=lid),
            claims=decode_claims(_tokens.make_token(l_id=lid)),
        )
        for lid in (_tokens.LEGA_CLASSIC, _tokens.LEGA_MANTRA)
    ]

    assert TokenStore(session, cipher).save(captured, now=NOW) == 2
    # two upserts plus exactly one touch_last_seen for the whole run
    assert len(session.statements) == 3


def test_the_ciphertext_written_is_not_the_token() -> None:
    cipher = a_cipher()
    plaintext = _tokens.make_token(l_id=1)

    assert plaintext.encode() not in cipher.encrypt(plaintext)


# --- SC 11: no key, and it still answers ----------------------------------


def test_status_works_with_no_cipher_at_all() -> None:
    """SC 11, by construction: `status()` never touches the cipher."""
    rows = [
        (
            _tokens.LEGA_MANTRA,
            "Legamiallerotaie2",
            "4f2a1c8e",
            NOW,
            NOW + timedelta(days=357),
            NOW,
            NOW,
            None,
            None,
            None,
        )
    ]
    store = TokenStore(_Session(rows), cipher=None)

    statuses = store.status()

    assert [s.league_id for s in statuses] == [_tokens.LEGA_MANTRA]
    assert isinstance(statuses[0], TokenStatus)
    assert statuses[0].expires_at == NOW + timedelta(days=357)


def test_load_plaintext_without_a_key_raises_key_missing_not_attribute_error() -> None:
    """A store with no key must say so, not fail on an attribute of None."""
    store = TokenStore(_Session(), cipher=None)

    with pytest.raises(KeyMissing):
        store.load_plaintext(_tokens.LEGA_MANTRA)


def test_the_fingerprint_property_is_none_without_a_key() -> None:
    assert TokenStore(_Session(), cipher=None).key_fingerprint is None


# --- the three refusals ---------------------------------------------------


def test_a_missing_row_names_the_lega_and_the_command() -> None:
    store = TokenStore(_Session(None), a_cipher())

    with pytest.raises(TokenMissing) as caught:
        store.load_plaintext(9911111)

    assert "9911111" in str(caught.value)
    assert "fantabot auth login" in str(caught.value)


def test_an_expired_row_is_refused_before_it_is_decrypted() -> None:
    """The check is local, so the caller never opens a socket to be told."""
    cipher = a_cipher()
    session = _Session(a_stored_row(cipher, expires_at=NOW - timedelta(days=1)))

    with pytest.raises(TokenExpired) as caught:
        TokenStore(session, cipher).load_plaintext(_tokens.LEGA_MANTRA, now=NOW)

    assert str(_tokens.LEGA_MANTRA) in str(caught.value)
    assert "fantabot auth login" in str(caught.value)


def test_a_fingerprint_mismatch_names_both_before_fernet_is_reached() -> None:
    """SC 15. Reaching Fernet would produce `InvalidToken` and lose the cause."""
    cipher, other = a_cipher(), a_cipher()
    session = _Session(a_stored_row(other, fingerprint=other.fingerprint))

    with pytest.raises(TokenUndecryptable) as caught:
        TokenStore(session, cipher).load_plaintext(_tokens.LEGA_MANTRA)

    message = str(caught.value)
    assert other.fingerprint in message
    assert cipher.fingerprint in message
    assert "InvalidToken" not in message


def test_no_refusal_message_contains_the_token() -> None:
    cipher, other = a_cipher(), a_cipher()
    plaintext = _tokens.make_token(l_id=_tokens.LEGA_MANTRA)
    session = _Session(a_stored_row(other, plaintext=plaintext, fingerprint=other.fingerprint))

    with pytest.raises(TokenUndecryptable) as caught:
        TokenStore(session, cipher).load_plaintext(_tokens.LEGA_MANTRA)

    message = str(caught.value)
    leaked = [
        plaintext[i : i + 8]
        for i in range(len(plaintext) - 7)
        if plaintext[i : i + 8] in message
    ]
    assert leaked == []


# --- forget ---------------------------------------------------------------


def test_forget_reports_whether_there_was_a_row() -> None:
    assert TokenStore(_Session(True), a_cipher()).forget(1) is True
    assert TokenStore(_Session(False), a_cipher()).forget(1) is False
