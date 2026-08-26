"""lineup.py's no-resubmit guard, without a browser or a database.

run_once cannot be called end to end — three DOM stubs still raise — but the
guard's two properties are structural and worth pinning now, because getting
either wrong is silent: a lineup submitted twice, or a matchday skipped for good.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

SOURCE = Path("src/fantabot/lineup.py").read_text()


def test_the_guard_is_scoped_to_one_lega() -> None:
    """The flat file it replaces had one matchday for both leghe, so submitting
    in one marked the other done."""
    assert "settings.fantabot_league_id" in SOURCE
    assert "last_lineup_matchday(league_id)" in SOURCE


def test_the_matchday_is_recorded_only_after_the_submit_returns() -> None:
    """Marking it first would make a failed submit look done and skip the
    matchday permanently."""
    submit_at = SOURCE.index("submit_lineup(page, lineup)")
    record_at = SOURCE.index("record_lineup_submitted")

    assert submit_at < record_at


def test_lineup_no_longer_goes_through_the_flat_state_file() -> None:
    assert "state.load()" not in SOURCE
    assert "state.save(" not in SOURCE


def test_importing_lineup_opens_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """It sits on the browser import chain, and `fantabot --help` has to keep
    working with the compose stack down."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a connection was opened at import time")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    import fantabot.lineup  # noqa: F401
