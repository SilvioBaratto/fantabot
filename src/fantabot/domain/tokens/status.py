"""What `fantabot auth status` renders. Pure — no session, no key, no network.

SC 11 (`auth status` answers with the key absent) and SC 12 (`ORPHANED`) are
display logic, and putting them here rather than in `store.py` or as SQL in the
repository is what makes them testable with no database and no key at all. Same
pure/shell split CLAUDE.md already mandates.

**A sixth module under `tokens/` where SPEC's Project Structure lists five** —
recorded as a departure in the token-store phase's plan (archived; not in this
checkout, and not in git history either — `tasks/` has always been gitignored).

`TokenStatus` is defined here, and it is the input type of both `orphaned()` and
`render_state()`. `adapters/persistence/repositories/tokens.py` imports it and
constructs the values, so the dependency points **persistence → `tokens.status`
and never back**: under `mypy --strict` these parameters need real annotations,
and a type owned by the repository would drag a `fantabot.adapters.persistence`
import into the module whose whole purpose is to have none.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TokenStatus:
    """One `league_tokens` row as the operator sees it, detached from its session.

    A plain value, never a live ORM instance: `auth status` renders these long
    after the session has closed, and a lazy attribute would either trip a query
    or raise `DetachedInstanceError` in front of the operator.

    **Carries no ciphertext.** Nothing that renders a status needs one, so
    nothing that renders a status is given one.
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


def orphaned(rows: Iterable[TokenStatus]) -> set[int]:
    """Leghe a later login looked for and did not find.

    A row is orphaned when its `last_seen_at` is strictly behind the newest stamp
    in the table. Self-contained — no extra table, no network — which is what
    keeps `auth status` an offline read.

    Empty for zero rows, and empty for one row: the only lega you have is the one
    you just saw, so a single-row table can never be orphaned. Empty, too, when
    every stamp is equal, which is the normal state after any full login.
    """
    stamps = list(rows)
    if len(stamps) < 2:
        return set()
    newest = max(row.last_seen_at for row in stamps)
    return {row.league_id for row in stamps if row.last_seen_at < newest}


def render_state(
    row: TokenStatus,
    *,
    now: datetime,
    key_fingerprint: str | None,
    is_orphaned: bool = False,
) -> str:
    """One cell of the status table.

    Precedence, most-blocking first: a key mismatch means nothing about the row
    can be trusted, so it outranks expiry; an expired token outranks orphaning
    because it is the one with an action attached; and an orphaned row is
    reported last because SPEC is explicit that its token is still valid.

    `key_fingerprint` is `None` when no key is configured — and the expiry
    columns are plaintext precisely so this function still works then. That is
    SC 11, satisfied by construction rather than by remembering to test it.
    """
    mismatch = key_mismatch(row.key_fingerprint, key_fingerprint)
    if mismatch is not None:
        return mismatch
    if now >= row.expires_at:
        return f"EXPIRED {row.expires_at:%Y-%m-%d}"
    if is_orphaned:
        return f"ORPHANED — last seen {row.last_seen_at:%Y-%m-%d}"
    return f"ok ({(row.expires_at - now).days}d)"


def key_mismatch(row_fingerprint: str, key_fingerprint: str | None) -> str | None:
    """The KEY MISMATCH cell, or `None` when the row can be read with the configured key.

    One wording for every stored credential. The FantaLab session had no state at all, so
    a session saved under another key — unreadable by anything — rendered exactly like a
    working one on the Accounts page. `None` for `key_fingerprint` means no key is
    configured, which is not a mismatch: there is nothing to compare against.
    """
    if key_fingerprint is not None and row_fingerprint != key_fingerprint:
        return f"KEY MISMATCH (row {row_fingerprint}, .env {key_fingerprint})"
    return None


def session_state(row_fingerprint: str, *, key_fingerprint: str | None) -> str:
    """One FantaLab session's status cell: `ok`, or the same KEY MISMATCH a lega row gets.

    No expiry branch: the session row carries no expiry of its own, so the only thing this
    screen can know without a decrypt is whether the key that wrote it is the one we hold.
    """
    return key_mismatch(row_fingerprint, key_fingerprint) or "ok"



def is_usable(row: TokenStatus, *, now: datetime, key_fingerprint: str) -> bool:
    """Can this row be used to act, with the key we hold, at this moment?

    The same two facts `render_state` ranks, as a decision instead of a sentence —
    and in one place, because they were in two and the second only checked expiry.
    `auth status` said KEY MISMATCH while `auth login` said "All stored tokens
    valid" about the very same row, and the command that repairs a dead credential
    was the one that got it wrong.

    `key_fingerprint` is required, not optional. `render_state` takes `None` because
    a status table must still render its plaintext expiry columns with no key
    configured; a caller *acting* on a token has a key by then, and defaulting to
    "assume it matches" is the defect this function exists to remove.
    """
    return row.key_fingerprint == key_fingerprint and now < row.expires_at


MISSING = "MISSING"
"""A lega known to exist with no row.

Only two things can make a lega *known*: `settings.fantabot_league_id` when it is
non-zero, and an id passed to `--league`. An otherwise-empty table therefore
prints "no tokens stored" rather than inventing lega ids to call missing.
"""
