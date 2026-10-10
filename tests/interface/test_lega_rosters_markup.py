"""A team name is platform free text and must reach the table as text, not as Rich markup."""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from fantabot.interface import lega


def _team(nome: str, owner: str) -> SimpleNamespace:
    return SimpleNamespace(
        nome=nome,
        owner=owner,
        roster=[],
        credits_spent=0,
        credits_remaining=500,
    )


def test_stray_closing_tag_in_name_and_owner_prints_literally(monkeypatch) -> None:
    buffer = io.StringIO()
    monkeypatch.setattr(lega, "console", Console(file=buffer, width=200))
    result = SimpleNamespace(rosters=[_team("FC [/x]", "Mario [/y]")])

    lega._print_rosters(result)  # type: ignore[arg-type]

    out = buffer.getvalue()
    assert "FC [/x]" in out
    assert "Mario [/y]" in out
