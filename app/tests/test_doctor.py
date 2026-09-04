"""S13 — `fantabot-app doctor` environment checks."""

from __future__ import annotations

from typer.testing import CliRunner

from fantabot_app.cli import app
from fantabot_app.doctor import Check, run_checks

runner = CliRunner()


def test_run_checks_reports_python_ok() -> None:
    checks = run_checks()
    python = next(check for check in checks if check.name == "python")
    assert python.ok is True


def test_every_check_has_the_expected_shape() -> None:
    for check in run_checks():
        assert isinstance(check, Check)
        assert isinstance(check.name, str) and check.name
        assert isinstance(check.ok, bool)
        assert isinstance(check.detail, str) and check.detail


def test_doctor_command_prints_a_report() -> None:
    result = runner.invoke(app, ["doctor"])
    # exit 0 when all pass, 1 when a check fails — either way it prints the report
    assert "python:" in result.output
    assert "fantabot:" in result.output
    assert "chromium:" in result.output
