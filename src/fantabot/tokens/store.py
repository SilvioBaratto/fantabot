"""The one place a stored token is decrypted. The only I/O module under `tokens/`.

`tests/test_token_secrecy.py` scans `src/fantabot/` for that call and allows it
in exactly two files: `crypto.py`, which defines it, and this one, which makes
the single call. Everything else — `apileague.py` included — goes through
`load_plaintext`.

`status()` never touches the cipher. That is not a convention to remember; it is
why SC 11 holds. The expiry columns are plaintext, so a store built with
`cipher=None` can still answer "is it expired" — which is precisely the moment
an operator most needs a straight answer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from fantabot.db.models.tokens import LeagueToken
from fantabot.db.repositories.tokens import LeagueTokenRepository
from fantabot.tokens.capture import CapturedToken
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.errors import KeyMissing, TokenExpired, TokenMissing, TokenUndecryptable
from fantabot.tokens.status import TokenStatus


class TokenStore:
    """Encrypted tokens, and the plaintext columns beside them.

    `cipher` is optional so `token-status` can be built without a key. Every
    method that needs one says so by raising `KeyMissing`, never by failing on
    an attribute of `None`.
    """

    def __init__(self, session: Session, cipher: TokenCipher | None = None) -> None:
        self._repo = LeagueTokenRepository(session)
        self._cipher = cipher

    @property
    def key_fingerprint(self) -> str | None:
        """The configured key's fingerprint, or `None` when there is no key."""
        return self._cipher.fingerprint if self._cipher is not None else None

    def _require_cipher(self) -> TokenCipher:
        if self._cipher is None:
            raise KeyMissing()
        return self._cipher

    def save(self, captured: list[CapturedToken], *, now: datetime) -> int:
        """Encrypt and store every captured lega. Returns how many were written.

        **One `now` for the whole run**, so `last_seen_at` is exactly comparable
        across leghe and `status.orphaned()` is exact rather than
        nearly-right — two `datetime.now()` calls a millisecond apart would make
        the first lega look orphaned by the second.

        Every captured lega is stamped, whether or not its ciphertext changed:
        `login --league X` sees the whole `leagues[]` array while replacing only
        X, and a lega seen but not rewritten must not drift into looking
        ORPHANED.
        """
        cipher = self._require_cipher()

        for one in captured:
            self._repo.upsert(
                LeagueToken(
                    league_id=one.league_id,
                    ciphertext=cipher.encrypt(one.token),
                    key_fingerprint=cipher.fingerprint,
                    issued_at=one.claims.issued_at,
                    expires_at=one.claims.expires_at,
                    user_id=one.claims.user_id,
                    team_id=one.claims.team_id,
                    league_name=one.league_name,
                    captured_at=now,
                    last_seen_at=now,
                    last_verified_at=None,
                )
            )
        self._repo.touch_last_seen([one.league_id for one in captured], now)
        return len(captured)

    def load_plaintext(self, league_id: int, *, now: datetime | None = None) -> str:
        """The bearer token for one lega, or a sentence explaining why not.

        The expiry check happens **here**, before any caller opens a socket —
        which is the difference between "your token expired on 2027-08-19, run
        `fantabot login`" and a bare `401` from a server.
        """
        cipher = self._require_cipher()

        row = self._repo.get(league_id)
        if row is None:
            raise TokenMissing(league_id)

        if now is not None and now >= row.expires_at:
            raise TokenExpired(league_id, f"{row.expires_at:%Y-%m-%d}")

        return cipher.decrypt(row.ciphertext, stored_fingerprint=row.key_fingerprint)

    def status(self) -> list[TokenStatus]:
        """Every stored lega, from plaintext columns only.

        Never touches the cipher — SC 11 by construction rather than by
        remembering to test it.
        """
        return self._repo.all_rows()

    def forget(self, league_id: int) -> bool:
        """Delete one lega's row. `True` if there was one to delete."""
        return self._repo.delete(league_id)

    def mark_verified(self, league_id: int, at: datetime) -> None:
        self._repo.mark_verified(league_id, at)


__all__ = ["TokenStore", "TokenUndecryptable"]
