"""Reading and writing the encrypted FantaLab session.

The only place a FantaLab credential is decrypted, mirroring what
``tokens/store.py`` does for lega tokens. Keeping that surface to one function
per service is what makes the secrecy test able to say anything: it asserts
``decrypt(`` appears nowhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from fantabot.db.models.tokens import FantalabSession as FantalabSessionRow
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.fantalab import FantalabSession


class FantalabStore:
    """The encrypted session, keyed by the account's FantaLab uuid."""

    def __init__(self, session: Session, cipher: TokenCipher) -> None:
        self.session = session
        self.cipher = cipher

    def save(self, captured: FantalabSession, *, now: datetime | None = None) -> None:
        """Store or replace the session. Replaced rather than versioned.

        A superseded refresh token stays valid at the provider until it is
        rotated there, so keeping old rows would mean keeping live credentials
        with no way to say which is current.
        """
        moment = now or datetime.now(UTC)
        statement = insert(FantalabSessionRow).values(
            user_id=captured.user_id,
            ciphertext=self.cipher.encrypt(captured.as_blob()),
            key_fingerprint=self.cipher.fingerprint,
            captured_at=moment,
        )
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "ciphertext": statement.excluded.ciphertext,
                    "key_fingerprint": statement.excluded.key_fingerprint,
                    "captured_at": statement.excluded.captured_at,
                    # Cleared on re-capture: a new session has not been used yet,
                    # and carrying the old stamp forward would claim otherwise.
                    "last_used_at": None,
                },
            )
        )

    def load(self, user_id: str | None = None) -> FantalabSession | None:
        """The stored session, or ``None``.

        With no ``user_id`` the most recently captured row wins, so the common
        single-account case needs no argument and a multi-account one is still
        addressable.
        """
        statement = select(FantalabSessionRow).order_by(
            FantalabSessionRow.captured_at.desc()
        )
        if user_id is not None:
            statement = statement.where(FantalabSessionRow.user_id == user_id)
        row = self.session.execute(statement).scalars().first()
        if row is None:
            return None
        blob = self.cipher.decrypt(row.ciphertext, stored_fingerprint=row.key_fingerprint)
        return FantalabSession.from_blob(row.user_id, blob)

    def mark_used(self, user_id: str, *, now: datetime | None = None) -> None:
        row = self.session.get(FantalabSessionRow, user_id)
        if row is not None:
            row.last_used_at = now or datetime.now(UTC)

    def describe(self) -> list[tuple[str, datetime, datetime | None]]:
        """``(user_id, captured_at, last_used_at)`` per stored session.

        Deliberately returns no ciphertext and no fingerprint of anything but
        the key, so a status command can be written without a decrypt.
        """
        rows = self.session.execute(
            select(
                FantalabSessionRow.user_id,
                FantalabSessionRow.captured_at,
                FantalabSessionRow.last_used_at,
            ).order_by(FantalabSessionRow.captured_at.desc())
        ).all()
        return [(r.user_id, r.captured_at, r.last_used_at) for r in rows]
