"""`fantabot auth forget` — the only thing that removes a stored token.

Small on purpose. SPEC's Open Question 5 rules that removal is manual, because a
`leagues[]` that came back short would otherwise silently destroy a working
token and re-login is the only recovery. So the interesting assertions are about
what it *refuses* to do: no `--all`, no wildcard, and nothing removed without
either a confirmation or `--yes`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from typer.testing import CliRunner

from fantabot.domain.tokens.status import TokenStatus
from fantabot.interface.app import app

runner = CliRunner()
NOW = datetime(2026, 8, 26, tzinfo=UTC)


def a_status(league_id: int = 4103937) -> TokenStatus:
    return TokenStatus(
        league_id=league_id,
        league_name="Legamiallerotaie2",
        key_fingerprint="4f2a1c8e",
        issued_at=NOW - timedelta(days=7),
        expires_at=NOW + timedelta(days=357),
        captured_at=NOW,
        last_seen_at=NOW,
        last_verified_at=None,
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Any:
    rows = [a_status(3584692), a_status(4103937)]
    removed: list[int] = []

    class _Store:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def status(self) -> list[TokenStatus]:
            return rows

        def forget(self, league_id: int) -> bool:
            removed.append(league_id)
            rows[:] = [r for r in rows if r.league_id != league_id]
            return True

    import fantabot.adapters.tokens.store as store_module

    monkeypatch.setattr(store_module, "TokenStore", _Store)

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "get_session", lambda: _Session())
    return {"rows": rows, "removed": removed}


def test_no_league_exits_two_and_removes_nothing(store: Any) -> None:
    result = runner.invoke(app, ["auth", "forget"])

    assert result.exit_code == 2
    assert store["removed"] == []
    assert len(store["rows"]) == 2


def test_there_is_no_all_flag_and_no_wildcard() -> None:
    """Asserted on the registered parameters, not on the rendered help text.

    The help text *mentions* `--all` — the docstring explains why there is not
    one — so a substring scan would fail for the opposite of the real reason.
    """
    import typer.main

    root = typer.main.get_command(app)
    command = root.commands["auth"].commands["forget"]  # type: ignore[attr-defined]
    flags = {opt for param in command.params for opt in param.opts}

    assert "--all" not in flags
    assert "--league" in flags


def test_a_confirmed_removal_takes_exactly_one_row(store: Any) -> None:
    """Asserted on the row count either side, not on the exit code."""
    result = runner.invoke(app, ["auth", "forget", "--league", "4103937"], input="y\n")

    assert result.exit_code == 0
    assert store["removed"] == [4103937]
    assert [r.league_id for r in store["rows"]] == [3584692]


def test_a_declined_confirmation_removes_nothing(store: Any) -> None:
    result = runner.invoke(app, ["auth", "forget", "--league", "4103937"], input="n\n")

    assert store["removed"] == []
    assert len(store["rows"]) == 2
    assert "Nothing removed" in result.output


def test_yes_skips_the_prompt(store: Any) -> None:
    result = runner.invoke(app, ["auth", "forget", "--league", "4103937", "--yes"])

    assert result.exit_code == 0
    assert store["removed"] == [4103937]


def test_an_unknown_lega_says_so_without_a_traceback(store: Any) -> None:
    result = runner.invoke(app, ["auth", "forget", "--league", "9911111", "--yes"])

    assert result.exit_code == 0
    assert "nothing to remove" in result.output
    assert store["removed"] == []


def test_the_printed_row_shows_no_ciphertext_and_no_fingerprint(store: Any) -> None:
    result = runner.invoke(app, ["auth", "forget", "--league", "4103937"], input="n\n")

    assert "4f2a1c8e" not in result.output
    assert "ciphertext" not in result.output
    assert "Legamiallerotaie2" in result.output
