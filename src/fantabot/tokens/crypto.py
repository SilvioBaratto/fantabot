"""Encrypt and decrypt one league token. Holds the key, never the plaintext.

Pure in the sense that matters: the key arrives as an argument, so this module
imports nothing from `fantabot.config` and every test constructs its own cipher
with a throwaway key.

Fernet rather than raw AES-GCM — authenticated by construction, no nonce for us
to get wrong, and a key that is a single opaque base64 string that copies cleanly
into `.env`. The cost is AES-128 rather than 256, which is not the weak link when
the key lives beside the data it protects.
"""

from __future__ import annotations

import hashlib

from cryptography.fernet import Fernet, InvalidToken

from fantabot.tokens.errors import KeyMalformed, KeyMissing, TokenUndecryptable

FINGERPRINT_LENGTH = 8


class TokenCipher:
    """One key, and the two operations performed with it."""

    def __init__(self, key: str) -> None:
        # This guard MUST stay before the constructor. `Fernet("")` raises
        # ValueError just like `Fernet("not-a-key")` does, so without it an unset
        # key reports as malformed and the operator is told to fix the shape of a
        # key they never set. Measured against cryptography 50.0.0.
        if not key:
            raise KeyMissing()
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise KeyMalformed() from exc
        # A hash prefix of the *key*, never of the plaintext, so it reveals
        # nothing about any token. 8 hex characters; the column is varchar(16),
        # which is headroom rather than a mismatch.
        self.fingerprint: str = hashlib.sha256(key.encode()).hexdigest()[:FINGERPRINT_LENGTH]

    def __repr__(self) -> str:
        # Explicit, though this class keeps no `key` attribute and the default
        # would be safe today. It exists so a future drive-by `@dataclass` cannot
        # silently make it unsafe — precisely the class of change this phase
        # defends against.
        return f"<TokenCipher fingerprint={self.fingerprint}>"

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes, *, stored_fingerprint: str) -> str:
        """Decrypt, or say *why*. A mismatched key is not a corrupt row.

        `stored_fingerprint` is keyword-only so no caller can drop it
        positionally and silently skip the check that makes the failure legible.

        Without the fingerprint, a changed key produces
        `cryptography.fernet.InvalidToken` — five words describing the symptom
        and not the cause, which sends the operator looking for data corruption
        that is not there.
        """
        if stored_fingerprint != self.fingerprint:
            raise TokenUndecryptable(
                f"this row was encrypted with key {stored_fingerprint}, but "
                f"FANTABOT_ENCRYPTION_KEY is {self.fingerprint}. Restore the old "
                "key, or re-run `fantabot login` to replace the row."
            )
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise TokenUndecryptable(
                "the stored token failed its authenticity check — the row is "
                "corrupt. Re-run `fantabot login`."
            ) from exc
