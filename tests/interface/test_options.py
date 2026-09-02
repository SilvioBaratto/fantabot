"""Every 0-1-ish CLI knob refuses `nan` and `inf` before a cycle ever runs.

`click.FloatRange` compares with `<`/`>`, and every comparison against `nan` is `False` — a
`FloatRange(0.0, 1.0)` accepts `nan` for the exact reason it looks like it should reject it.
Reproduced live: `--bargain-share nan` reaches `RoomTracker.cycle` at the shipped *disabled*
default (`--bargain-beta 0.00`) and raises `ValueError: cannot convert float NaN to integer`
from inside a poll, because `bargain_allowance` is computed unconditionally every cycle. The
fix is a boundary validator, not a call-site guard, so it covers every 0-1 knob this module
declares — present and future — the same way.

These tests exercise the real `app` through `CliRunner`, not a throwaway Typer app: Click
parameter validation runs before a command's body executes, so `asta room`/`asta bid` never
touch the network, FantaLab or Postgres here — a rejected value never reaches `import` inside
the function, which is why `asta optimize`'s `database_manager` import staying unexecuted is
part of what each assertion below proves, not an incidental detail.
"""

from __future__ import annotations

from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()


def _rejected_before_any_cycle(args: list[str]) -> None:
    """The value never reaches a command body: exit 2, no leaked exception."""
    result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def _rejected_by_the_finite_check(args: list[str]) -> None:
    """As above, and specifically by `_reject_non_finite` — not by `min=`/`max=` alone.

    A fully bounded range (`min=0.0, max=1.0`) already refuses `inf` on its own — `inf` fails
    the ordinary `x <= max` comparison — so that one case is asserted generically instead
    (`_rejected_before_any_cycle`); asserting the callback's wording there would depend on
    Click picking one particular rejection over another that also holds.
    """
    result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "finite" in result.output


class TestTiltKRejectsNonFinite:
    def test_nan_is_rejected(self) -> None:
        _rejected_by_the_finite_check(["asta", "optimize", "--tilt-k", "nan"])

    def test_inf_is_rejected(self) -> None:
        # Bounded both sides: Click's own range check catches this before the callback runs.
        _rejected_before_any_cycle(["asta", "optimize", "--tilt-k", "inf"])

    def test_a_finite_value_is_not_rejected_by_this_callback(self) -> None:
        # No database is wired here, so the command still fails past parsing — but not on
        # "must be a finite number", which is the one thing this test guards against.
        result = runner.invoke(app, ["asta", "optimize", "--tilt-k", "0.5"])
        assert "finite" not in result.output


class TestCeilingAlphaRejectsNonFinite:
    def test_nan_is_rejected_on_asta_room(self) -> None:
        _rejected_by_the_finite_check(
            ["asta", "room", "https://app.fantalab.it/asta?asta=fake", "--ceiling-alpha", "nan"]
        )

    def test_inf_is_rejected_on_asta_room(self) -> None:
        # CeilingAlpha declares only a lower bound, so `inf` is not caught by min=/max= at all —
        # this is the case a bounded-below-only knob needs the callback for.
        _rejected_by_the_finite_check(
            ["asta", "room", "https://app.fantalab.it/asta?asta=fake", "--ceiling-alpha", "inf"]
        )


class TestBargainBetaRejectsNonFinite:
    def test_nan_is_rejected_on_asta_room(self) -> None:
        _rejected_by_the_finite_check(
            ["asta", "room", "https://app.fantalab.it/asta?asta=fake", "--bargain-beta", "nan"]
        )


class TestBargainShareRejectsNonFinite:
    def test_nan_is_rejected_on_asta_room(self) -> None:
        """The exact reproduction: `--bargain-share nan` at the shipped disabled default."""
        _rejected_by_the_finite_check(
            ["asta", "room", "https://app.fantalab.it/asta?asta=fake", "--bargain-share", "nan"]
        )

    def test_nan_is_rejected_on_asta_bid(self) -> None:
        _rejected_by_the_finite_check(
            [
                "asta", "bid",
                "--league", "x", "--db", "1", "--team", "x", "--user", "x",
                "--bargain-share", "nan",
            ]
        )
