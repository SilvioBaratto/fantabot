"""`fantabot lineup` — read/plan/submit the weekly Mantra formazione. **Zero sockets.**

The network call (`apileague.teamLineup_read`) and the database session are both faked, so
the shell is exercised without Postgres or a token, in the socket-free default tier. The
pure formatter is tested directly; the command is a thin wrapper around it.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from fantabot.interface.app import app
from fantabot.interface.lineup import format_lineup

runner = CliRunner()

DTO = {
    "mdl": "343",
    "starts": [6482, 2788, 7564, 7274, 7181, 1850, 5504, 5678, 2194, 6875, 4179],
    "bench": [4360, 5750, 4137, 4998, 5620, 5680, 4459, 6898, 7198, 4947, 5319, 7126],
}


# --- the pure formatter ---------------------------------------------------


def test_format_lineup_names_the_module_and_counts_the_lines() -> None:
    text = "\n".join(format_lineup(DTO))

    assert "343" in text
    assert "11" in text  # starters
    assert "12" in text  # bench
    assert "6482" in text


def test_format_lineup_says_when_no_lineup_is_set() -> None:
    assert "no lineup" in " ".join(format_lineup({})).lower()


# --- the CLI shell --------------------------------------------------------


class _Session:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _fakes(monkeypatch: pytest.MonkeyPatch, dto: dict[str, Any] = DTO) -> None:
    from fantabot import config
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings, "fantabot_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(database_manager, "_session_factory", _Session)
    monkeypatch.setattr(
        apileague, "teamLineup_read", lambda *a, **k: {"teamLineupDto": dto, "lineUpInfo": []}
    )


def test_show_renders_the_current_lineup(monkeypatch: pytest.MonkeyPatch) -> None:
    _fakes(monkeypatch)

    result = runner.invoke(app, ["lineup", "show", "--competition", "311681"])

    assert result.exit_code == 0
    assert "343" in result.output


def test_show_requires_a_competition(monkeypatch: pytest.MonkeyPatch) -> None:
    _fakes(monkeypatch)

    result = runner.invoke(app, ["lineup", "show"])

    assert result.exit_code != 0
    assert "competition" in result.output.lower()
