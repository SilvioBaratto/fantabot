"""The league bearer token: decoding it, encrypting it, and where it is kept.

Three pure modules and one shell, the same split as `news/`:

* `claims` — decode a JWT payload. Values in, values out.
* `crypto` — Fernet encrypt/decrypt. Takes its key as an argument.
* `capture` — a Playwright `storage_state` dict into league-checked tokens.
* `status` — what `token-status` renders. Pure.
* `store`  — the one place a stored token is decrypted. The only I/O here.

The first four import nothing from `fantabot.db`, `fantabot.config`, `playwright`
or `httpx`, which is what makes the interesting cases testable without a socket.

**`TokenStore` is deliberately NOT re-exported here**, though SPEC's Project
Structure asks for it. Re-exporting it creates a real import cycle:
`db.repositories.tokens` imports `tokens.status`, which executes this file,
which would import `tokens.store`, which imports `db.repositories.tokens` —
partially initialised. The test suite caught it the moment it was tried.

The cycle is the pure/shell boundary asserting itself: `db` may depend on the
pure half of `tokens`, so the pure half must not reach back through a package
import. Callers say `from fantabot.tokens.store import TokenStore`.
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
from fantabot.tokens.status import TokenStatus, orphaned, render_state

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
    "TokenStatus",
    "TokenUndecryptable",
    "TokenUnreadable",
    "orphaned",
    "render_state",
]
