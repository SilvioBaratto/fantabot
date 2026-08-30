"""The FantaLab session: what it is, and how it is read out of a browser. Pure.

Measured on 2026-08-27, by listing storage **key names only** — no value was
read, returned or stored:

```
localStorage   refresh_token · id_token · access_token · user_id · user_email
cookies        analytics only — no auth cookie
IndexedDB      present, but not what the app reads
```

Two facts follow, and both shape this module.

**Playwright's ``storage_state`` captures localStorage but not IndexedDB.** Had
FantaLab relied on the Firebase SDK's own store, a saved session would have been
worthless. It does not, so a capture is possible at all.

**But that state must never be written to a file.** A ``storage_state.json`` on
disk would hold three credentials in the clear — exactly what the token-store
phase exists to prevent. The values go from browser memory to Fernet to Postgres
with no plaintext stop in between, which is why this module returns a value type
rather than a path.

``refresh_token`` is the durable credential. The ID token expires in about an
hour and the app re-derives one on every page load via ``/sign-in``, so storing
that alone would produce a login that works until the moment you need it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fantabot.domain.tokens.errors import TokenError

ORIGIN = "https://app.fantalab.it"

#: Captured because the app reads them back. ``user_email`` is deliberately not
#: here: it sits in the same store, we have no use for it, and keeping personal
#: data with no purpose is a cost with no benefit.
CREDENTIAL_KEYS = ("refresh_token", "id_token", "access_token")


@dataclass(frozen=True)
class FantalabSession:
    """One account's FantaLab session.

    ``repr`` is overridden rather than inherited. The default would print all
    three credentials into any traceback, log line or debugger frame that
    touched an instance — and CLAUDE.md's rule is that a bearer token is never
    printed, logged or ``repr``'d in any form, truncated or whole.
    """

    user_id: str
    refresh_token: str
    id_token: str | None = None
    access_token: str | None = None

    def __repr__(self) -> str:
        return f"<FantalabSession user_id={self.user_id}>"

    def as_blob(self) -> str:
        """The whole session as one JSON string, to be encrypted as a unit.

        One ciphertext rather than three columns: a partial write cannot then
        leave two of the three stored, and rotating the key rewrites one value.
        """
        return json.dumps(
            {key: getattr(self, key) for key in CREDENTIAL_KEYS}, sort_keys=True
        )

    @classmethod
    def from_blob(cls, user_id: str, blob: str) -> FantalabSession:
        values = json.loads(blob)
        return cls(user_id=user_id, **{key: values.get(key) for key in CREDENTIAL_KEYS})


def parse_fantalab_storage(storage_state: Mapping[str, Any]) -> FantalabSession:
    """Read a session out of a Playwright ``storage_state`` mapping.

    Only ``app.fantalab.it``'s own origin is considered. A shared browser
    profile carries other sites' storage, and reading a token from one of them
    would be both wrong and a privacy breach.
    """
    entries: dict[str, str] = {}
    for origin in storage_state.get("origins", []):
        if origin.get("origin") != ORIGIN:
            continue
        for item in origin.get("localStorage", []):
            name, value = item.get("name"), item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                entries[name] = value

    if not entries:
        raise TokenError(
            f"No {ORIGIN} storage found. Sign in, wait for the page to finish "
            "loading, then press Enter."
        )
    if not entries.get("refresh_token"):
        raise TokenError(
            "No refresh_token in storage. The id_token alone expires in about an "
            "hour, so storing it would produce a login that works until you need it."
        )
    if not entries.get("user_id"):
        raise TokenError("No user_id in storage — the session cannot be keyed.")

    return FantalabSession(
        user_id=entries["user_id"],
        refresh_token=entries["refresh_token"],
        id_token=entries.get("id_token"),
        access_token=entries.get("access_token"),
    )
