"""S2 — /auth/status (account status, no key required)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.auth import build_auth_status

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _row(league_id: int, expires: datetime, *, seen: datetime | None = None):
    from fantabot.domain.tokens.status import TokenStatus

    return TokenStatus(
        league_id=league_id,
        league_name=f"Lega {league_id}",
        key_fingerprint="abcd1234",
        issued_at=NOW - timedelta(days=1),
        expires_at=expires,
        captured_at=NOW - timedelta(days=1),
        last_seen_at=seen or NOW,
        last_verified_at=None,
        user_id=1,
        team_id=2,
    )


def test_build_auth_status_renders_ok_and_expired_states() -> None:
    rows = [_row(1, NOW + timedelta(days=30)), _row(2, NOW - timedelta(days=1))]
    status = build_auth_status(rows, [("user9", NOW, None)], now=NOW, has_key=True)

    states = {league.league_id: league.state for league in status.leagues}
    assert states[1].startswith("ok")
    assert states[2].startswith("EXPIRED")
    assert status.fantalab[0].user_id == "user9"
    assert status.has_key is True


def test_build_auth_status_flags_orphaned_league() -> None:
    # league 2 last seen earlier than league 1 -> orphaned
    rows = [_row(1, NOW + timedelta(days=30), seen=NOW), _row(2, NOW + timedelta(days=30), seen=NOW - timedelta(days=2))]
    status = build_auth_status(rows, [], now=NOW, has_key=False)
    states = {league.league_id: league.state for league in status.leagues}
    assert states[1].startswith("ok")
    assert "ORPHANED" in states[2]


def test_auth_status_endpoint_degrades_open_on_db_error(monkeypatch) -> None:
    # Force the DB layer to fail so the test opens no socket (zero-socket default tier)
    # and exercises the degrade-open path.
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/auth/status")
    assert response.status_code == 200
    body = response.json()
    assert body["leagues"] == []
    assert body["fantalab"] == []
    assert "has_key" in body


def test_a_row_under_another_key_reads_as_key_mismatch() -> None:
    """The app's half of `d206dd9`, which fixed only the CLI's.

    `render_state` ranks a key mismatch above expiry because "a key mismatch means nothing
    about the row can be trusted". This endpoint passed `key_fingerprint=None`, so that
    branch was dead by construction and a dead credential rendered `ok (Nd)` — while
    `fantabot auth status`, against the same database and the same key, printed KEY MISMATCH.

    The operator's own machine on 2026-09-10 is the case: both leghe stored under key
    `aa695c77`, `.env` holding `ef341176`. The terminal said both were unusable; the
    Accounts page painted both green, and `has_key` was `true` so its no-key warning did
    not fire either.
    """
    rows = [_row(1, NOW + timedelta(days=300))]  # row fingerprint is "abcd1234"

    status = build_auth_status(
        rows, [], now=NOW, has_key=True, key_fingerprint="ef341176"
    )

    assert "KEY MISMATCH" in status.leagues[0].state
    assert "abcd1234" in status.leagues[0].state, "the row's key is not named"
    assert "ef341176" in status.leagues[0].state, "the configured key is not named"


def test_a_matching_key_still_reads_ok() -> None:
    """The negative control: naming a fingerprint must not make every row a mismatch."""
    rows = [_row(1, NOW + timedelta(days=300))]

    status = build_auth_status(
        rows, [], now=NOW, has_key=True, key_fingerprint="abcd1234"
    )

    assert status.leagues[0].state.startswith("ok")


def test_no_key_configured_still_renders_the_plaintext_expiry() -> None:
    """SC 11, and the reason `render_state`'s fingerprint is optional in the first place.

    `expires_at` is a plaintext column precisely so a status read works with no key at all.
    Passing a fingerprint must not become mandatory: with none, the page still says when
    each token runs out, and simply cannot speak to whether it opens.
    """
    rows = [_row(1, NOW + timedelta(days=300)), _row(2, NOW - timedelta(days=1))]

    status = build_auth_status(rows, [], now=NOW, has_key=False, key_fingerprint=None)

    states = {league.league_id: league.state for league in status.leagues}
    assert states[1].startswith("ok")
    assert states[2].startswith("EXPIRED")
    assert not any("KEY MISMATCH" in s for s in states.values())
