"""The league bearer token: decoding it, encrypting it, and where it is kept.

Three pure modules and one shell, the same split as `news/`:

* `claims` — decode a JWT payload. Values in, values out.
* `crypto` — Fernet encrypt/decrypt. Takes its key as an argument.
* `capture` — a Playwright `storage_state` dict into league-checked tokens.
* `status` — what `token-status` renders. Pure.
* `store`  — the one place a stored token is decrypted. The only I/O here.

The first four import nothing from `fantabot.db`, `fantabot.config`, `playwright`
or `httpx`, which is what makes the interesting cases testable without a socket.
"""

from fantabot.tokens.errors import (
    ApiTimeout,
    ApiUnavailable,
    AppKeyRejected,
    KeyMalformed,
    KeyMissing,
    LeagueMismatch,
    NoLeaguesFound,
    TokenError,
    TokenExpired,
    TokenMissing,
    TokenRejected,
    TokenUndecryptable,
    TokenUnreadable,
)

__all__ = [
    "ApiTimeout",
    "ApiUnavailable",
    "AppKeyRejected",
    "KeyMalformed",
    "KeyMissing",
    "LeagueMismatch",
    "NoLeaguesFound",
    "TokenError",
    "TokenExpired",
    "TokenMissing",
    "TokenRejected",
    "TokenUndecryptable",
    "TokenUnreadable",
]
