"""Account status and the headed connect-account login.

Status is a pure read: no key required (``TokenStore.status`` and ``render_state`` work
with the key absent — SC 11), no network, no decrypt. Login is the interactive, headed
flow: it launches the real browser (never a scripted one — the flow navigates once and
clicks nothing), you sign in by hand, and tokens are encrypted to the DB.

Because ``auth_login.run`` **blocks until you say you are signed in**, it runs on a
background job thread and that block waits on a per-job gate which
``POST /auth/login/{job_id}/confirm`` releases — the web equivalent of pressing Enter.

Detecting completion automatically was built and reverted with the evidence in hand:
polling the browser's storage opened a burst of tabs over the login form every two
seconds, because ``storage_state()`` navigates a temporary page to every visited origin
and the site's ad iframes leave theirs behind. See ``application/login_wait``.

The gate's timeout is a raise, not a fall-through. It used to be ``gate.wait(timeout)``
with the result discarded, so an abandoned login woke after ten minutes and read storage
anyway — storing whatever happened to be in the browser.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from fantabot.domain.tokens.status import TokenStatus, orphaned, render_state
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fantabot_app.api.infrastructure.jobs import BufferingReporter, registry

router = APIRouter()

#: Per-login-job gates: the blocking prompt waits, the confirm endpoint releases.
_login_gates: dict[str, threading.Event] = {}
_LOGIN_TIMEOUT_S = 600.0


class LoginAbandoned(Exception):
    """Nobody confirmed within the deadline."""

    def __init__(self) -> None:
        super().__init__(
            "no confirmation within 10 minutes. Nothing was written — start the "
            "connection again when you are ready to sign in."
        )


def _gate_prompt(reporter: BufferingReporter, gate: threading.Event) -> Callable[[str], str]:
    """A prompt that shows its message in the job log and blocks until confirmed.

    Raises rather than returning when the deadline passes. The earlier version
    discarded `wait`'s result, so an abandoned login fell through to the read and
    stored whatever was in the browser ten minutes later.
    """

    def prompt(message: str) -> str:
        # The web's own gesture. The use case states the condition and each interface
        # supplies the gesture, so this must not echo the message verbatim — the CLI's
        # "Press Enter" in a browser is an instruction the reader cannot follow.
        reporter.print(f"Click Continue {message}.")
        reporter.awaiting_confirm = True
        try:
            if not gate.wait(timeout=_LOGIN_TIMEOUT_S):
                raise LoginAbandoned()
        finally:
            # Cleared even on the timeout, so a dead job never looks like one still
            # asking. The flow may come back and ask again — an early confirmation is
            # answered with another prompt, not a failure.
            reporter.awaiting_confirm = False
        gate.clear()
        return ""

    return prompt


class LeagueTokenStatus(BaseModel):
    league_id: int
    league_name: str | None
    state: str
    expires_at: datetime
    last_verified_at: datetime | None = None
    user_id: int | None = None
    team_id: int | None = None


class FantalabSessionStatus(BaseModel):
    user_id: str
    captured_at: datetime
    last_used_at: datetime | None = None


class AuthStatus(BaseModel):
    leagues: list[LeagueTokenStatus]
    fantalab: list[FantalabSessionStatus]
    has_key: bool


def build_auth_status(
    token_rows: Sequence[TokenStatus],
    fantalab_rows: Sequence[tuple[str, datetime, datetime | None]],
    *,
    now: datetime,
    has_key: bool,
) -> AuthStatus:
    """Assemble the response from TokenStatus rows and FantaLab describe() tuples (pure)."""
    orphaned_ids = orphaned(token_rows)
    leagues = [
        LeagueTokenStatus(
            league_id=row.league_id,
            league_name=row.league_name,
            state=render_state(
                row,
                now=now,
                key_fingerprint=None,  # no-key read; never claims KEY MISMATCH
                is_orphaned=row.league_id in orphaned_ids,
            ),
            expires_at=row.expires_at,
            last_verified_at=row.last_verified_at,
            user_id=row.user_id,
            team_id=row.team_id,
        )
        for row in token_rows
    ]
    fantalab = [
        FantalabSessionStatus(user_id=user_id, captured_at=captured_at, last_used_at=last_used_at)
        for (user_id, captured_at, last_used_at) in fantalab_rows
    ]
    return AuthStatus(leagues=leagues, fantalab=fantalab, has_key=has_key)


@router.get("/auth/status", response_model=AuthStatus, tags=["auth"])
def auth_status() -> AuthStatus:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings

    has_key = bool(settings.fantabot_encryption_key)
    try:
        with database_manager.get_session() as session:
            token_rows = TokenStore(session).status()
            fantalab_rows = FantalabSessionRepository(session).describe()
        return build_auth_status(
            token_rows, fantalab_rows, now=datetime.now(UTC), has_key=has_key
        )
    except Exception:  # noqa: BLE001 — degrade open: no DB / no rows -> "not connected"
        return AuthStatus(leagues=[], fantalab=[], has_key=has_key)


class JobStarted(BaseModel):
    job_id: str


@router.post("/auth/login", response_model=JobStarted, tags=["auth"])
def auth_login_start(league: int = 0, force: bool = False) -> JobStarted:
    """Open the headed browser and, after you confirm, store the encrypted tokens.

    `force` is what makes a disconnect recoverable. Without it `auth_login.run`
    short-circuits whenever every *remaining* stored token is still valid: after
    disconnecting one of two leghe, the surviving one satisfies that check, so the
    run reports "All stored tokens valid. No browser opened" and stores nothing —
    stranding the lega that was just removed. The UI sends force=true when any
    league token is still stored.
    """
    gate = threading.Event()

    def job(reporter: BufferingReporter) -> object:
        from fantabot.adapters.browser import capture
        from fantabot.application import auth_login

        return auth_login.run(
            league=league,
            force=force,
            browser_factory=capture.real_browser,
            read_state=capture.read_storage_state,
            report=reporter,
            prompt=_gate_prompt(reporter, gate),
            verify=True,
        )

    job_id = registry.start(job)
    _login_gates[job_id] = gate
    return JobStarted(job_id=job_id)


@router.post("/auth/fantalab-login", response_model=JobStarted, tags=["auth"])
def fantalab_login_start(browser: str = "", force: bool = False) -> JobStarted:
    """Open the headed browser for FantaLab and, after you confirm, store the session.

    Same reason as the lega login above: `fantalab_login.run` refuses to open a
    browser while any session is stored, so without `force` a re-capture is only
    possible after a disconnect.
    """
    gate = threading.Event()

    def job(reporter: BufferingReporter) -> object:
        from fantabot.adapters.browser import capture
        from fantabot.application import fantalab_login

        return fantalab_login.run(
            force=force,
            browser_factory=lambda: capture.real_browser(browser or None),
            read_state=capture.read_storage_state,
            report=reporter,
            prompt=_gate_prompt(reporter, gate),
        )

    job_id = registry.start(job)
    _login_gates[job_id] = gate
    return JobStarted(job_id=job_id)


@router.post("/auth/login/{job_id}/confirm", tags=["auth"])
def auth_login_confirm(job_id: str) -> dict[str, bool]:
    """Release a login job blocked on its prompt — the web equivalent of pressing Enter."""
    gate = _login_gates.get(job_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="unknown login job")
    gate.set()
    return {"ok": True}


class ForgetResult(BaseModel):
    """What a disconnect removed. Counts only — never anything about the credential."""

    ok: bool
    removed: bool
    rows_removed: int


# The two disconnects below are the only mutating routes that do NOT degrade open, and
# that is deliberate. The session context manager commits on clean exit, i.e. *inside*
# any `try` wrapped around it, so the read endpoints' `except Exception -> empty state`
# would turn a rolled-back delete into a 200 saying the credential was removed. A failed
# delete has to be a 500: the operator must know the rows are still there.
#
# The path parameter is required, with no default. `auth_login_start`'s `league: int = 0`
# is safe because 0 means "every lega"; here a defaulted 0 would silently mean "lega 0"
# and report a cheerful removed=false. That shape mirrors `fantabot auth forget`, which
# exits 2 on a missing --league and deliberately has no --all — but only that shape:
# the CLI still removes the token alone, while this route removes the whole lega.


@router.delete("/auth/league/{league_id}", response_model=ForgetResult, tags=["auth"])
def auth_forget_league(league_id: int) -> ForgetResult:
    """Remove one lega: its stored token and every row `lega sync` wrote for it.

    Disconnecting used to take the token alone, which left the lega on the Dashboard,
    Asta and Prices pages — every screen but the one that said it was disconnected.

    One session, therefore one commit (the context manager commits at exit): either the
    whole lega goes or none of it does. The token is removed **last** on purpose, so a
    failure part-way leaves the operator the one handle they can act on.

    Needs no encryption key: `TokenStore.forget` takes the keyless path — the store's
    cipher is optional and only the save/read paths require it — so an operator who has
    lost the key can still clear rows it can no longer open.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.league import LeagueRepository
    from fantabot.adapters.tokens.store import TokenStore

    with database_manager.get_session() as session:
        purged = LeagueRepository(session).purge(league_id)
        removed = TokenStore(session).forget(league_id)
    return ForgetResult(ok=True, removed=removed, rows_removed=sum(purged.values()))


@router.delete("/auth/fantalab/{user_id}", tags=["auth"])
def auth_forget_fantalab(user_id: str) -> dict[str, bool]:
    """Remove one FantaLab session. Needs no encryption key.

    Goes straight to the repository rather than through `FantalabStore`, whose cipher
    is mandatory: `auth_status` above already reads this table the same way, and a
    removal has no more need of the key than a status read does.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository

    with database_manager.get_session() as session:
        removed = FantalabSessionRepository(session).delete(user_id)
    return {"ok": True, "removed": removed}
