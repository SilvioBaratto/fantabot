"""`league_tokens` against a real Postgres. Five things only real SQL can prove.

`@pytest.mark.db`, so deselected by default. The `db_session` fixture wraps each
test in a transaction that is always rolled back, so this leaves the table
exactly as it found it.

Every token here is synthesized by `tests/_tokens.py`. A real one has never been
in this repository.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import _tokens
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.models.tokens import LeagueToken
from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository
from fantabot.adapters.tokens.store import TokenStore
from fantabot.domain.tokens.crypto import TokenCipher
from fantabot.domain.tokens.errors import TokenUndecryptable

pytestmark = pytest.mark.db

NOW = datetime(2026, 8, 26, tzinfo=UTC)
LEGA = _tokens.LEGA_MANTRA


def a_cipher() -> TokenCipher:
    return TokenCipher(Fernet.generate_key().decode())


def a_row(cipher: TokenCipher, token: str, **overrides: object) -> LeagueToken:
    fields: dict[str, object] = {
        "league_id": LEGA,
        "ciphertext": cipher.encrypt(token),
        "key_fingerprint": cipher.fingerprint,
        "issued_at": datetime.fromtimestamp(_tokens.IAT, tz=UTC),
        "expires_at": datetime.fromtimestamp(_tokens.EXP, tz=UTC),
        "user_id": _tokens.USER_ID,
        "team_id": _tokens.TEAM_MANTRA,
        "league_name": "Legamiallerotaie2",
        "captured_at": NOW,
        "last_seen_at": NOW,
        "last_verified_at": None,
    }
    fields.update(overrides)
    return LeagueToken(**fields)


def test_a_token_survives_a_real_bytea_round_trip(db_session: Session) -> None:
    """SC 4, byte for byte.

    The type assertion is the point. psycopg2 can hand back a `memoryview` for
    `bytea`, which every equality check here would still pass — and then
    `Fernet.decrypt` would choke in production on a value the tests called fine.
    """
    cipher = a_cipher()
    token = _tokens.make_token(l_id=LEGA, t_id=_tokens.TEAM_MANTRA)
    LeagueTokenRepository(db_session).upsert(a_row(cipher, token))
    db_session.flush()
    db_session.expunge_all()

    stored = db_session.execute(
        select(LeagueToken).where(LeagueToken.league_id == LEGA)
    ).scalar_one()

    assert isinstance(stored.ciphertext, bytes), (
        f"bytea came back as {type(stored.ciphertext).__name__}; Fernet.decrypt "
        "needs bytes"
    )
    assert cipher.decrypt(stored.ciphertext, stored_fingerprint=stored.key_fingerprint) == token


def test_expires_at_equals_the_tokens_own_exp_claim(db_session: Session) -> None:
    """SC 4. The column and the claim must not drift apart."""
    from fantabot.domain.tokens.claims import decode_claims

    cipher = a_cipher()
    token = _tokens.make_token(l_id=LEGA)
    LeagueTokenRepository(db_session).upsert(a_row(cipher, token))
    db_session.flush()

    stored = db_session.execute(
        select(LeagueToken.expires_at).where(LeagueToken.league_id == LEGA)
    ).scalar_one()

    assert stored == decode_claims(token).expires_at


def test_upserting_twice_leaves_one_row_carrying_the_second_key(
    db_session: Session,
) -> None:
    """SC 8, and the fingerprint trap, against real SQL rather than a compiled
    statement. A row with the second ciphertext beside the first fingerprint
    would tell the operator to restore a key that is not the problem."""
    first, second = a_cipher(), a_cipher()
    repo = LeagueTokenRepository(db_session)

    repo.upsert(a_row(first, _tokens.make_token(l_id=LEGA)))
    db_session.flush()
    later_token = _tokens.make_token(l_id=LEGA, iat=_tokens.IAT + 1)
    repo.upsert(a_row(second, later_token))
    db_session.flush()
    db_session.expunge_all()

    rows = db_session.execute(
        select(LeagueToken).where(LeagueToken.league_id == LEGA)
    ).scalars().all()

    assert len(rows) == 1, "the upsert accumulated rows instead of replacing"
    assert rows[0].key_fingerprint == second.fingerprint
    assert second.decrypt(rows[0].ciphertext, stored_fingerprint=rows[0].key_fingerprint) == (
        later_token
    )


def test_a_row_written_with_one_key_and_read_with_another_names_both(
    db_session: Session,
) -> None:
    """SC 15, end to end through the store."""
    written, current = a_cipher(), a_cipher()
    LeagueTokenRepository(db_session).upsert(a_row(written, _tokens.make_token(l_id=LEGA)))
    db_session.flush()

    with pytest.raises(TokenUndecryptable) as caught:
        TokenStore(db_session, current).load_plaintext(LEGA)

    message = str(caught.value)
    assert written.fingerprint in message
    assert current.fingerprint in message
    assert "InvalidToken" not in message


def test_an_upsert_resets_a_previously_written_last_verified_at(
    db_session: Session,
) -> None:
    """A new credential is not verified because its predecessor was."""
    cipher = a_cipher()
    repo = LeagueTokenRepository(db_session)

    repo.upsert(a_row(cipher, _tokens.make_token(l_id=LEGA)))
    db_session.flush()
    repo.mark_verified(LEGA, NOW)
    db_session.flush()

    assert db_session.execute(
        select(LeagueToken.last_verified_at).where(LeagueToken.league_id == LEGA)
    ).scalar_one() == NOW

    repo.upsert(a_row(cipher, _tokens.make_token(l_id=LEGA, iat=_tokens.IAT + 1)))
    db_session.flush()

    assert (
        db_session.execute(
            select(LeagueToken.last_verified_at).where(LeagueToken.league_id == LEGA)
        ).scalar_one()
        is None
    )


def test_the_stored_column_is_not_readable_as_text(db_session: Session) -> None:
    """What Adminer shows. `bytea` is a signpost as much as a storage choice."""
    cipher = a_cipher()
    token = _tokens.make_token(l_id=LEGA)
    LeagueTokenRepository(db_session).upsert(a_row(cipher, token))
    db_session.flush()

    rendered = db_session.execute(
        text("SELECT ciphertext::text FROM league_tokens WHERE league_id = :lid"),
        {"lid": LEGA},
    ).scalar_one()

    assert token not in str(rendered)
    assert "eyJ" not in str(rendered)


def test_touch_last_seen_moves_only_the_leghe_named(db_session: Session) -> None:
    """The split `login --league X` depends on: seen is not the same as rewritten."""
    cipher = a_cipher()
    repo = LeagueTokenRepository(db_session)
    for lid in (_tokens.LEGA_CLASSIC, LEGA):
        repo.upsert(a_row(cipher, _tokens.make_token(l_id=lid), league_id=lid))
    db_session.flush()

    later = NOW + timedelta(days=1)
    repo.touch_last_seen([LEGA], later)
    db_session.flush()
    db_session.expunge_all()

    stamps = dict(
        db_session.execute(
            select(LeagueToken.league_id, LeagueToken.last_seen_at).order_by(
                LeagueToken.league_id
            )
        ).all()
    )

    assert stamps[LEGA] == later
    assert stamps[_tokens.LEGA_CLASSIC] == NOW
