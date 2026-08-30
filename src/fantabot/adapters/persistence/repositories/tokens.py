"""Reads and writes over `league_tokens` and `fantalab_session`. Bytes only.

This module imports no cipher, and no decryption happens here.
That is the store's job (`tokens/store.py`), which is the single decryption site
in the codebase — a boundary `tests/test_token_secrecy.py` enforces with a
source scan, so this file must not even name the call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from fantabot.adapters.persistence.models.tokens import FantalabSession, LeagueToken
from fantabot.adapters.persistence.repositories._base import RepositoryBase
from fantabot.domain.tokens.status import TokenStatus

if TYPE_CHECKING:
    from sqlalchemy import CursorResult

# Everything the upsert must overwrite: the whole table minus the key it
# conflicts on and the timestamp that records first insertion. Derived, never
# hand-written — a hand-written list is exactly how `key_fingerprint` gets
# dropped, and this way a column added later fails the test rather than being
# silently skipped.
_IMMUTABLE = frozenset({"league_id", "created_at"})
UPSERT_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in LeagueToken.__table__.columns if c.name not in _IMMUTABLE
)

# Columns whose value must come from the server, never from the ORM instance.
# `updated_at` carries a server_default, and that default only fires when the
# column is *absent* from the INSERT — passing `getattr(row, "updated_at")` on an
# unflushed instance sends an explicit NULL and violates the NOT NULL constraint.
# It still belongs in the SET clause, so a replaced row's timestamp moves.
_SERVER_TIMESTAMPS = frozenset({"updated_at"})


class LeagueTokenRepository(RepositoryBase):
    """One row per lega, replaced rather than versioned."""

    def upsert(self, row: LeagueToken) -> None:
        """Write a captured token, replacing whatever was there.

        **Every mutable column is in the `SET` clause, including
        `key_fingerprint`.** Omitting it is not hypothetical: after a key change
        plus a re-login the row would carry a *new* ciphertext beside the *old*
        fingerprint, `decrypt` would say "this row was encrypted with key X —
        restore the old key", and the operator would do precisely the wrong
        thing. The fingerprint mechanism inverted into a trap, silently.

        `last_verified_at` resets to NULL for the same reason: a new credential
        has not been verified merely because its predecessor was.
        """
        values = {
            name: getattr(row, name)
            for name in UPSERT_COLUMNS
            if name not in _SERVER_TIMESTAMPS
        }
        values["last_verified_at"] = None

        statement = insert(LeagueToken).values(league_id=row.league_id, **values)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[LeagueToken.league_id],
                set_={
                    name: (
                        func.now()
                        if name in _SERVER_TIMESTAMPS
                        else statement.excluded[name]
                    )
                    for name in UPSERT_COLUMNS
                },
            )
        )

    def get(self, league_id: int) -> LeagueToken | None:
        return self.session.execute(
            select(LeagueToken).where(LeagueToken.league_id == league_id)
        ).scalar_one_or_none()

    def all_rows(self) -> list[TokenStatus]:
        """Every stored lega, ordered.

        The `ORDER BY` is explicit because Postgres has no inherent row order,
        and `auth status`'s output would otherwise shuffle between runs for no
        reason the operator could explain.
        """
        rows = self.session.execute(
            select(
                LeagueToken.league_id,
                LeagueToken.league_name,
                LeagueToken.key_fingerprint,
                LeagueToken.issued_at,
                LeagueToken.expires_at,
                LeagueToken.captured_at,
                LeagueToken.last_seen_at,
                LeagueToken.last_verified_at,
                LeagueToken.user_id,
                LeagueToken.team_id,
            ).order_by(LeagueToken.league_id)
        ).all()
        return [TokenStatus(*row) for row in rows]

    def delete(self, league_id: int) -> bool:
        """Remove one lega's row. ``True`` if there was one.

        `Session.execute` is typed as returning `Result`, which has no
        `rowcount` — only `CursorResult` does. The narrowing is explicit rather
        than a `type: ignore`, so a future change that stops returning a cursor
        result fails here instead of at runtime.
        """
        result = self.session.execute(
            sql_delete(LeagueToken).where(LeagueToken.league_id == league_id)
        )
        return bool(cast("CursorResult[Any]", result).rowcount)

    def touch_last_seen(self, league_ids: Sequence[int], at: datetime) -> None:
        """Stamp the leghe this login found, whether or not it rewrote them.

        Separate from `upsert` on purpose: `login --league X` sees every lega in
        `leagues[]` but replaces only X's ciphertext, and a lega seen but not
        rewritten must not drift into looking ORPHANED.
        """
        if not league_ids:
            return
        self.session.execute(
            update(LeagueToken)
            .where(LeagueToken.league_id.in_(list(league_ids)))
            .values(last_seen_at=at)
        )

    def mark_verified(self, league_id: int, at: datetime) -> None:
        """Record that this token answered a real request with a 200."""
        self.session.execute(
            update(LeagueToken)
            .where(LeagueToken.league_id == league_id)
            .values(last_verified_at=at)
        )


@dataclass(frozen=True)
class FantalabSessionRecord:
    """One stored FantaLab session as the store reads it back.

    Carries the ciphertext, because the store is the thing that decrypts it — the
    same division `LeagueTokenRepository` already keeps. What it deliberately does
    not carry is anything derived from the plaintext.
    """

    user_id: str
    ciphertext: bytes
    key_fingerprint: str


class FantalabSessionRepository(RepositoryBase):
    """Every query over `fantalab_session`.

    `FantalabStore` used to issue these itself, which made it the only credential
    path in the repo that reached SQLAlchemy directly — against this package's own
    rule that every query the application makes lives behind a repository. The
    cipher stays in the store; only the SQL moved.
    """

    def upsert(self, *, user_id: str, ciphertext: bytes, fingerprint: str, at: datetime) -> None:
        """Store or replace. Replaced rather than versioned.

        A superseded refresh token stays valid at the provider until it is rotated
        there, so keeping old rows would mean keeping live credentials with no way
        to say which is current.
        """
        statement = insert(FantalabSession).values(
            user_id=user_id,
            ciphertext=ciphertext,
            key_fingerprint=fingerprint,
            captured_at=at,
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

    def newest(self, user_id: str | None = None) -> FantalabSessionRecord | None:
        """The most recently captured session, optionally for one account."""
        statement = select(FantalabSession).order_by(FantalabSession.captured_at.desc())
        if user_id is not None:
            statement = statement.where(FantalabSession.user_id == user_id)
        row = self.session.execute(statement).scalars().first()
        if row is None:
            return None
        return FantalabSessionRecord(
            user_id=row.user_id,
            ciphertext=row.ciphertext,
            key_fingerprint=row.key_fingerprint,
        )

    def mark_used(self, user_id: str, at: datetime) -> None:
        row = self.session.get(FantalabSession, user_id)
        if row is not None:
            row.last_used_at = at

    def describe(self) -> list[tuple[str, datetime, datetime | None]]:
        """`(user_id, captured_at, last_used_at)` per stored session.

        Selects no ciphertext and no fingerprint, so a status command can be written
        without a decrypt — the same property `all_rows` has for lega tokens, and a
        test asserts both.
        """
        rows = self.session.execute(
            select(
                FantalabSession.user_id,
                FantalabSession.captured_at,
                FantalabSession.last_used_at,
            ).order_by(FantalabSession.captured_at.desc())
        ).all()
        return [(r.user_id, r.captured_at, r.last_used_at) for r in rows]
