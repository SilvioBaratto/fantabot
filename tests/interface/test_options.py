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


# -- one default per flag, across every command that declares it -------------------------

import click  # noqa: E402 — grouped with the tests that need it
import typer.main  # noqa: E402

from fantabot.interface.asta import DEFAULT_LAM  # noqa: E402


def _lam_defaults() -> dict[str, object]:
    """Every `asta` subcommand that takes `--lam`, and the default Click would apply.

    Asked of Typer's Click tree rather than grepped out of `--help`, for
    `test_cli_harvest_home.py::_param`'s reason: a boxed help row is prose, and "what does
    this option default to" is a property, not a number that happens to appear in the box.
    """
    root: click.Command = typer.main.get_command(app)
    group = root.get_command(click.Context(root), "asta")  # type: ignore[attr-defined]
    assert group is not None
    ctx = click.Context(group)
    defaults: dict[str, object] = {}
    for name in group.list_commands(ctx):  # type: ignore[attr-defined]
        command = group.get_command(ctx, name)  # type: ignore[attr-defined]
        for param in command.params:
            if param.name == "lam":
                defaults[name] = param.default
    return defaults


class TestEveryCommandOptimisesTheSameObjective:
    """`--lam` is the risk aversion in `sum(mu) - lam*Var`, so two defaults are two objectives.

    `asta optimize` shipped `0.0` while the five commands that touch a live room shipped
    `0.3`. `asta optimize` is the *offline preview* of what those commands do, so unless the
    operator remembered the flag the preview solved a different problem from the bidder —
    silently, since both numbers are legal. `README.md:164` writes the example as
    `--lam 0.3` explicitly, which is a doc working around a default rather than one
    documenting it, and two commands word their help as "as the live commands use".

    Written as "they all agree" rather than "optimize is 0.3", because a pinned literal on
    one command is exactly the shape that let six declarations drift in the first place.
    """

    def test_the_six_declarations_are_all_still_here(self) -> None:
        """Pinned so a rename cannot leave the agreement below with one case, or none —
        which is a comparison that passes by having nothing to compare."""
        assert sorted(_lam_defaults()) == [
            "bench", "bid", "calibrate", "live", "optimize", "room",
        ]

    def test_no_command_disagrees_with_another_about_lam(self) -> None:
        defaults = _lam_defaults()
        assert set(defaults.values()) == {DEFAULT_LAM}, (
            f"--lam defaults disagree across commands: {defaults}"
        )

    def test_the_offline_preview_defaults_to_what_the_live_commands_use(self) -> None:
        """The specific pair the drift was between, named so the failure says which."""
        defaults = _lam_defaults()
        assert defaults["optimize"] == defaults["bid"] == defaults["room"] == defaults["live"]

    def test_the_shared_default_is_the_live_value(self) -> None:
        """`0.3` is what the five live commands have always shipped and what `README.md`
        passes by hand; agreeing on `0.0` would satisfy the two tests above."""
        assert DEFAULT_LAM == 0.3
