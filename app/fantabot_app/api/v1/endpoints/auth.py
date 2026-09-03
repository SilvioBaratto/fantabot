"""Account status and the headed connect-account login.

Status is a pure read: no key required (``TokenStore.status`` and ``render_state`` work
with the key absent — SC 11), no network, no decrypt. Login (S3) is the interactive,
headed flow: it launches the real browser (never a scripted one — the flow navigates once
and clicks nothing), you sign in by hand, and tokens are encrypted to the DB.

Because ``auth_login.run`` **blocks on a prompt** ("press Enter once logged in") between
opening the browser and reading the tokens, it runs on a background job thread and the
blocking prompt waits on a per-job gate that ``POST /auth/login/{job_id}/confirm``
releases — that confirm is the web equivalent of pressing Enter.
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

# Per-login-job gates: the blocking prompt waits, the confirm endpoint releases.
_login_gates: dict[str, threading.Event] = {}
_LOGIN_TIMEOUT_S = 600.0


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


def _gate_prompt(reporter: BufferingReporter, gate: threading.Event) -> Callable[[str], str]:
    """A prompt that shows its message in the job log and blocks until confirmed."""

    def prompt(message: str) -> str:
        reporter.print(message)
        gate.wait(timeout=_LOGIN_TIMEOUT_S)
        gate.clear()
        return ""

    return prompt


@router.post("/auth/login", response_model=JobStarted, tags=["auth"])
def auth_login_start(league: int = 0) -> JobStarted:
    """Open the headed browser and, after you confirm, store the encrypted tokens."""
    gate = threading.Event()

    def job(reporter: BufferingReporter) -> object:
        from fantabot.adapters.browser import capture
        from fantabot.application import auth_login

        return auth_login.run(
            league=league,
            browser_factory=capture.real_browser,
            report=reporter,
            prompt=_gate_prompt(reporter, gate),
            verify=True,
        )

    job_id = registry.start(job)
    _login_gates[job_id] = gate
    return JobStarted(job_id=job_id)


@router.post("/auth/fantalab-login", response_model=JobStarted, tags=["auth"])
def fantalab_login_start(browser: str = "") -> JobStarted:
    """Open the headed browser for FantaLab and, after you confirm, store the session."""
    gate = threading.Event()

    def job(reporter: BufferingReporter) -> object:
        from fantabot.adapters.browser import capture
        from fantabot.application import fantalab_login

        return fantalab_login.run(
            force=False,
            browser_factory=lambda: capture.real_browser(browser or None),
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
