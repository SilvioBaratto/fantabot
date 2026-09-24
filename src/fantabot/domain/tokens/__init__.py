"""The league bearer token: decoding it, encrypting it, and where it is kept.

Three pure modules and one shell, the same split as `news/`:

* `claims` — decode a JWT payload. Values in, values out.
* `crypto` — Fernet encrypt/decrypt. Takes its key as an argument.
* `capture` — a Playwright `storage_state` dict into league-checked tokens.
* `status` — what `auth status` renders. Pure.
* `store`  — the one place a stored token is decrypted. The only I/O here.

The first four import nothing from `fantabot.adapters.persistence`, `fantabot.config`, `playwright`
or `httpx`, which is what makes the interesting cases testable without a socket.

**`TokenStore` is deliberately NOT re-exported here**, though SPEC's Project
Structure asks for it. Re-exporting it creates a real import cycle:
`db.repositories.tokens` imports `tokens.status`, which executes this file,
which would import `tokens.store`, which imports `db.repositories.tokens` —
partially initialised. The test suite caught it the moment it was tried.

The cycle is the pure/shell boundary asserting itself: `db` may depend on the
pure half of `tokens`, so the pure half must not reach back through a package
import. Callers say `from fantabot.adapters.tokens.store import TokenStore`.

That reasoning survives this file having nothing in it. Importing
`fantabot.domain.tokens.status` executes this module whatever it contains, so an
import added here is the cycle, and the emptiness is what keeps the door shut.
This file also carried a 16-name re-export block — the thirteen `errors` classes
plus `TokenStatus`/`orphaned`/`render_state`. Measured 2026-09-24 it had **zero**
importers: every consumer, `src/`, `app/` and `tests/` alike, names the submodule
(`from fantabot.domain.tokens.errors import ...`). Deleted, because a package
re-export nobody reads is a second spelling of every name in it, and it is the
spelling that would one day be extended to `store`.
"""
