"""Reading and writing the encrypted FantaLab session.

The only place a FantaLab credential is decrypted, mirroring what ``tokens/store.py``
does for lega tokens. Keeping that surface to one function per service is what makes
the secrecy test able to say anything: it asserts ``decrypt(`` appears nowhere else.

**The SQL moved out on 2026-08-30.** This was the one credential path in the repo that
reached SQLAlchemy directly, while ``TokenStore`` had always gone through
``LeagueTokenRepository`` — against ``db/repositories/__init__.py``'s own rule that every
query lives behind a repository. Only the queries moved;
``FantalabSessionRepository`` handles ciphertext as bytes and never names a cipher, and
the decrypt stays here, where the allowlist expects it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from fantabot.db.repositories.tokens import FantalabSessionRepository
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.fantalab import FantalabSession


class FantalabStore:
    """The encrypted session, keyed by the account's FantaLab uuid."""

    def __init__(self, session: Session, cipher: TokenCipher) -> None:
        self.session = session
        self.cipher = cipher
        self.rows = FantalabSessionRepository(session)

    def save(self, captured: FantalabSession, *, now: datetime | None = None) -> None:
        """Encrypt, then hand bytes to the repository."""
        self.rows.upsert(
            user_id=captured.user_id,
            ciphertext=self.cipher.encrypt(captured.as_blob()),
            fingerprint=self.cipher.fingerprint,
            at=now or datetime.now(UTC),
        )

    def load(self, user_id: str | None = None) -> FantalabSession | None:
        """The stored session, or ``None``.

        With no ``user_id`` the most recently captured row wins, so the common
        single-account case needs no argument and a multi-account one is still
        addressable.
        """
        record = self.rows.newest(user_id)
        if record is None:
            return None
        blob = self.cipher.decrypt(
            record.ciphertext, stored_fingerprint=record.key_fingerprint
        )
        return FantalabSession.from_blob(record.user_id, blob)

    def mark_used(self, user_id: str, *, now: datetime | None = None) -> None:
        self.rows.mark_used(user_id, now or datetime.now(UTC))

    def describe(self) -> list[tuple[str, datetime, datetime | None]]:
        """``(user_id, captured_at, last_used_at)`` per stored session.

        Deliberately returns no ciphertext and no fingerprint of anything but the key,
        so a status command can be written without a decrypt.
        """
        return self.rows.describe()
