"""Account status — which leghe have a stored token, and the FantaLab session.

A pure status read: no encryption key required (``TokenStore.status`` and
``render_state`` work with the key absent — SC 11), no network, no decrypt. Degrades
open: a DB hiccup yields empty lists, never a 500. The connect-account action that writes
tokens is a separate, headed flow (S3).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from fantabot.domain.tokens.status import TokenStatus, orphaned, render_state
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


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
