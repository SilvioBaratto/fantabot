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


def test_reports_the_encryption_key_present_when_set(monkeypatch) -> None:
    monkeypatch.setenv("FANTABOT_ENCRYPTION_KEY", "some-key")
    key_check = next(c for c in run_checks() if c.name == "encryption key")
    assert key_check.ok is True
    assert "some-key" not in key_check.detail  # never leak the key


def test_doctor_command_prints_a_report() -> None:
    result = runner.invoke(app, ["doctor"])
    # exit 0 when all pass, 1 when a check fails — either way it prints the report
    assert "python:" in result.output
    assert "fantabot:" in result.output
    assert "chromium:" in result.output


# -- the frozen-copy check -----------------------------------------------------------
#
# The defect it exists for, measured 2026-09-20: `uv tool install ./app` installs
# `fantabot_app` **by value** while `tool.uv.sources` makes `fantabot` editable, so the
# tool on PATH ran a frozen app half against a live library half. It was missing
# `schedule` and `harvest` entirely — every `fantabot-app schedule …` line in `CLAUDE.md`
# failed as written — and nothing reported it, for weeks.


def _tree(root) -> None:
    """A source tree's two halves, as `doctor` has to recognise them."""
    (root / "src" / "fantabot").mkdir(parents=True)
    (root / "src" / "fantabot" / "__init__.py").touch()
    (root / "app" / "fantabot_app").mkdir(parents=True)
    (root / "app" / "fantabot_app" / "__init__.py").touch()
    (root / "app" / "pyproject.toml").touch()


def test_a_frozen_copy_beside_a_source_tree_is_a_failure(tmp_path) -> None:
    from fantabot_app.doctor import compare_app_source

    _tree(tmp_path)
    frozen = tmp_path / "tools" / "fantabot-app" / "fantabot_app"
    frozen.mkdir(parents=True)

    check = compare_app_source(frozen, tmp_path / "src" / "fantabot" / "__init__.py")

    assert check.ok is False


def test_the_failure_names_both_paths_so_the_two_can_be_told_apart(tmp_path) -> None:
    """`schedule status`'s MOVED rule: one path is half an answer, because the question
    is which of two copies is running."""
    from fantabot_app.doctor import compare_app_source

    _tree(tmp_path)
    frozen = tmp_path / "tools" / "fantabot-app" / "fantabot_app"
    frozen.mkdir(parents=True)

    check = compare_app_source(frozen, tmp_path / "src" / "fantabot" / "__init__.py")

    assert str(frozen) in check.detail
    assert str(tmp_path / "app" / "fantabot_app") in check.detail


def test_the_failure_names_its_remedy(tmp_path) -> None:
    from fantabot_app.doctor import compare_app_source

    _tree(tmp_path)
    frozen = tmp_path / "tools" / "fantabot_app"
    frozen.mkdir(parents=True)

    check = compare_app_source(frozen, tmp_path / "src" / "fantabot" / "__init__.py")

    assert "uv tool install --force --editable ./app" in check.detail


def test_the_trees_own_package_is_in_step(tmp_path) -> None:
    from fantabot_app.doctor import compare_app_source

    _tree(tmp_path)

    check = compare_app_source(
        tmp_path / "app" / "fantabot_app", tmp_path / "src" / "fantabot" / "__init__.py"
    )

    assert check.ok is True


def test_a_symlinked_path_is_the_same_package(tmp_path) -> None:
    """`samefile`, not `Path` equality — `CLAUDE.md` records that lesson from `harvest
    adopt`, which deleted the landing zone it was asked to adopt by comparing paths."""
    from fantabot_app.doctor import compare_app_source

    _tree(tmp_path)
    link = tmp_path / "alias"
    link.symlink_to(tmp_path / "app" / "fantabot_app")

    check = compare_app_source(link, tmp_path / "src" / "fantabot" / "__init__.py")

    assert check.ok is True


def test_no_source_tree_to_compare_against_is_not_a_failure(tmp_path) -> None:
    """A plain wheel install of both halves is a legitimate install and nothing is wrong.

    Reporting it would be a red mark every GitHub user sees on a correct setup, which is
    the fastest way to teach someone to ignore the report.
    """
    from fantabot_app.doctor import compare_app_source

    installed = tmp_path / "site-packages" / "fantabot_app"
    installed.mkdir(parents=True)
    library = tmp_path / "site-packages" / "fantabot" / "__init__.py"
    library.parent.mkdir(parents=True)
    library.touch()

    check = compare_app_source(installed, library)

    assert check.ok is True
    assert "source tree" in check.detail


def test_the_check_is_in_the_report() -> None:
    names = [check.name for check in run_checks()]
    assert "fantabot-app" in names


def test_two_directories_beside_each_other_are_not_a_source_tree(tmp_path) -> None:
    """`src/` and `app/` side by side is a common enough shape to be a coincidence.

    Without `pyproject.toml` the anchor is a guess, and a wrong guess here tells someone
    with an unrelated layout to reinstall a tool that is fine. The guard said so in a
    docstring and nothing checked it: the mutation that drops it left every other test
    in this file green.
    """
    from fantabot_app.doctor import compare_app_source

    (tmp_path / "src" / "fantabot").mkdir(parents=True)
    (tmp_path / "src" / "fantabot" / "__init__.py").touch()
    (tmp_path / "app" / "fantabot_app").mkdir(parents=True)
    (tmp_path / "app" / "fantabot_app" / "__init__.py").touch()
    # and deliberately no `app/pyproject.toml`
    elsewhere = tmp_path / "site-packages" / "fantabot_app"
    elsewhere.mkdir(parents=True)

    check = compare_app_source(elsewhere, tmp_path / "src" / "fantabot" / "__init__.py")

    assert check.ok is True
    assert "source tree" in check.detail
