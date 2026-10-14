"""`lineup plan --model projection` when the opponent adjustment is on: say so, and show it.

The command only prints what the application hands it. Off, the output is the table it has
always been (`test_lineup_cli.py` pins that header); on, the operator reads which opponent each
player was priced against and by how much, and the header says the adjustment is part of the
number — a μ that moved is not the μ the table used to show.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from fantabot.application.lineup_projection import (
    PlayerLine,
    ProjectionOutcome,
    ProjectionReport,
)
from fantabot.domain.lineup.freshness import Freshness
from fantabot.domain.lineup.matchup import Adjustment
from fantabot.domain.lineup.models import PlannedLineup
from fantabot.interface.app import app
from fantabot.interface.lineup import format_projection_rows

runner = CliRunner()

NOTE = "opponent adjustment on: slope per point of opponent ppg: GK -0.93; 2 of 3 paired players adjusted"


def _report(*, adjusted: bool, note: str | None = None) -> ProjectionReport:
    plan = PlannedLineup(
        module="343", starts=(6482, 4179), bench=(4360,), competition=311681, mday=1, cmday=3,
        tid=10000003,
    )
    lines = (
        PlayerLine(6482, "GK", "P", 6.10, 0.98, 1.20, 6.05),
        PlayerLine(4179, "ATT", "A", 7.40, 0.60, 2.10, 6.30),
        PlayerLine(4360, "GK", "P", 5.20, 0.40, 1.80, 4.90),
    )
    adjustments = (
        {
            6482: Adjustment(6482, "INT", "ROM", 1.62, -0.31),
            4179: Adjustment(4179, "INT", "ROM", 1.62, +0.12),
        }
        if adjusted
        else {}
    )
    outcome = ProjectionOutcome(
        plans=(plan,), lines=lines, freshness=Freshness(True, (), ()), replacement=5.5,
        as_of=date(2026, 10, 14), seasons=("2025/26", "2026/27"),
        adjustments=adjustments,
        matchup_note=NOTE if note is None and adjusted else (note or ""),
    )
    return ProjectionReport(
        baseline=(plan,), projection=outcome, names={6482: "Sommer", 4179: "Lautaro"},
        competition=311681,
    )


class TestTheTable:
    def test_off_it_is_the_seven_columns_it_always_was(self) -> None:
        header, *rows = format_projection_rows(_report(adjusted=False))
        assert header == ("player", "role", "mu", "p", "sigma~", "value", "XI")
        assert all(len(row) == 7 for row in rows)

    def test_on_it_gains_the_opponent_and_the_signed_adjustment(self) -> None:
        header, *rows = format_projection_rows(_report(adjusted=True))
        assert header[-2:] == ("opp", "adj")
        assert rows[0][-2:] == ("ROM", "-0.31")
        assert rows[1][-2:] == ("ROM", "+0.12")

    def test_a_player_who_was_not_priced_shows_a_dash_not_a_zero(self) -> None:
        *_, unpriced = format_projection_rows(_report(adjusted=True))
        assert unpriced[-2:] == ("-", "-")

    def test_the_columns_before_them_are_the_same_either_way(self) -> None:
        _, *off = format_projection_rows(_report(adjusted=False))
        _, *on = format_projection_rows(_report(adjusted=True))
        assert [row[:7] for row in on] == off


def _invoke(monkeypatch: pytest.MonkeyPatch, report: ProjectionReport) -> str:
    from fantabot import config
    from fantabot.adapters.persistence import database_manager
    from fantabot.application import lineup_projection

    class _Session:
        def commit(self) -> None: ...

        def rollback(self) -> None: ...

        def close(self) -> None: ...

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(config.settings, "fantabot_league_id", 4103937)
    monkeypatch.setattr(database_manager, "_session_factory", _Session)
    monkeypatch.setattr(lineup_projection, "projection_for_league", lambda *a, **k: report)
    result = runner.invoke(app, ["lineup", "plan", "--model", "projection"])
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


class TestTheCommand:
    def test_on_the_header_says_the_adjustment_is_part_of_the_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _invoke(monkeypatch, _report(adjusted=True))
        assert "opponent adjustment on" in out
        assert re.search(r"opp\W+adj", out) and "ROM" in out and "-0.31" in out

    def test_a_skipped_adjustment_is_still_said_in_the_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        note = "opponent adjustment on, skipped: no completed season to fit on"
        out = _invoke(monkeypatch, _report(adjusted=False, note=note))
        assert "skipped: no completed season to fit on" in out
        assert not re.search(r"opp\W+adj", out)

    def test_off_nothing_about_it_is_printed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = _invoke(monkeypatch, _report(adjusted=False))
        assert "adjustment" not in out
        assert not re.search(r"opp\W+adj", out)

    def test_a_note_with_square_brackets_is_not_read_as_markup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        note = "opponent adjustment on, skipped: [bold]ValueError[/bold]"
        out = _invoke(monkeypatch, _report(adjusted=False, note=note))
        assert "[bold]ValueError[/bold]" in out

    def test_the_other_lines_are_unchanged_by_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        off = _invoke(monkeypatch, _report(adjusted=False))
        on = _invoke(monkeypatch, _report(adjusted=True))
        for line in ("as of 2026-10-14 over 2025/26..2026/27", "module 343", "E[fp]"):
            assert (line in off) == (line in on)
        assert "as of 2026-10-14 over 2025/26..2026/27" in on

