"""One lega's ``apileague`` bearer token, encrypted at rest.

``expires_at`` and ``issued_at`` are deliberately **plaintext** columns beside
the ciphertext. They are metadata, not credentials — knowing a token dies in
August does not help anyone use it — and both consumers need them without a key:

* ``fantabot token-status`` has to answer "is it expired" when
  ``FANTABOT_ENCRYPTION_KEY`` is missing or wrong, which is precisely the
  situation where a straight answer matters most.
* ``auth_headers`` has to refuse an expired token **before a socket opens**,
  which it cannot do if the expiry is inside the thing it is deciding whether to
  use.

So nobody later "improves" this by decrypting first: that would make both of
those impossible while looking tidier.

One row per lega, replaced rather than versioned. A superseded token is a live
credential until its ``exp``, and keeping one is keeping an attack surface for
no operational gain.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.db.base import Base, TimestampMixin


class LeagueToken(Base, TimestampMixin):
    """The encrypted bearer token for one lega, keyed by its ``l_id``."""

    __tablename__ = "league_tokens"

    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)

    # bytea, not text. A Fernet token is ASCII base64 and would store happily in
    # `text` — where Adminer renders it as a selectable string that looks exactly
    # like something you could paste somewhere. The type is a signpost as much as
    # a storage choice. No length argument: Postgres ignores one on bytea, and a
    # length in the model with none in the database is a spurious alembic diff.
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # First 8 hex of sha256(key) — of the *key*, never the plaintext, so it
    # reveals nothing about any token. This is what turns `InvalidToken` into
    # "this row was encrypted with key X, .env holds Y". 16 is headroom.
    key_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    team_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Display only, from `currentLeague.name`. Never keyed or joined on.
    league_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # The last login at which this lega appeared in `leagues[]`. A row behind the
    # newest stamp in the table is ORPHANED: a later login looked and did not
    # find it. Self-contained, so `token-status` stays an offline read.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NULL means never confirmed against the live API, which is different from
    # "confirmed and then it broke".
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        # No ciphertext, no fingerprint. A repr ends up in tracebacks, in pytest
        # failure output, and in cron logs.
        return f"<LeagueToken lega={self.league_id} expires={self.expires_at:%Y-%m-%d}>"
