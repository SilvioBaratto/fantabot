"""What `fantabot token-status` renders. Pure — no session, no key, no network.

`TokenRow` lives here rather than in `db/repositories/tokens.py` so the
dependency points **`db` → `tokens.status`, and never back.** Under
`mypy --strict` the functions below need real annotations for their row
parameter; annotating it with a type the repository owned would put a
`fantabot.db` import into a module whose whole purpose is to have none.

The repository and the store construct these values; nothing here knows how.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TokenRow:
    """One `league_tokens` row, detached from its session.

    A plain value, never a live ORM instance: `token-status` renders these long
    after the session has closed, and a lazy attribute would either trip a query
    or raise `DetachedInstanceError` in front of the operator.

    Carries no ciphertext. Nothing that renders a status needs one, so nothing
    that renders a status is given one.
    """

    league_id: int
    league_name: str | None
    key_fingerprint: str
    issued_at: datetime
    expires_at: datetime
    captured_at: datetime
    last_seen_at: datetime
    last_verified_at: datetime | None
    user_id: int | None = None
    team_id: int | None = None
