"""What `db price` puts on the screen.

The formatting used to live inside `pricing.run`, which is why the application layer
imported `rich.table` and why `tests/test_layers.py` carried an expected violation for
it. Moving it here was meant to change where the numbers are formatted and nothing else,
so these tests pin the output: the columns, the two mover lists, the counts line.

Nothing tested any of it before, so there was no way to tell a faithful move from a
rewrite. The one difference the move did introduce -- a `[bold]` on the flag-counts line
-- was found by writing this file and reverted.
"""

from __future__ import annotations

import math
from typing import ClassVar

import pytest
from rich.console import Console

from fantabot.application.pricing import (
    FadeSummary,
    PricingReport,
    RoleFade,
    TargetPriceRow,
)
from fantabot.interface.app import render_pricing


def _row(nome: str, qi: int, target: int, flags: str = "") -> TargetPriceRow:
    return TargetPriceRow(
        id="1", nome=nome, squadra="NAP", role="c", macro_role="MID", qi=qi,
        prior_media_fantavoto=6.0, predicted_pct_delta=0.0, team_factor=1.0,
        target_price=target, flags=flags,
    )


class TestRenderPricing:
    REPORT: ClassVar[PricingReport] = PricingReport(
        system="mantra",
        fades=(
            FadeSummary(
                role="MID",
                observations=142,
                fade=RoleFade(
                    slope=-0.125, intercept=0.5,
                    clamp_lo=math.log(0.5), clamp_hi=math.log(2.0),
                ),
            ),
        ),
        team_factors={"NAP": 0.91},
        stored=548,
        biggest_bumps=(_row("Bumped", 10, 30),),
        biggest_cuts=(_row("Cut", 30, 10, "floor_qi"),),
        flag_counts={"floor_qi": 12},
    )

    @pytest.fixture
    def output(self, monkeypatch: pytest.MonkeyPatch) -> str:
        """Render into a fixed-width console, so the table does not wrap on the runner."""
        import fantabot.interface.app as app

        console = Console(width=200, force_terminal=False, no_color=True)
        monkeypatch.setattr(app, "console", console)
        with console.capture() as captured:
            render_pricing(self.REPORT, top_n=15)
        return captured.get()

    def test_it_names_what_was_fitted_and_on_what_scale(self, output: str) -> None:
        """`log(qa/qi)`, not `pct_delta` — the reader has to know which."""
        assert "mantra: fitted role fades (log(qa/qi) ~ prior_media_fantavoto, OLS):" in output

    def test_the_fade_table_carries_the_observation_count(self, output: str) -> None:
        """A slope from 20 observations and one from 400 are not the same claim."""
        assert "142" in output

    def test_the_slope_and_intercept_are_signed_to_three_places(self, output: str) -> None:
        assert "-0.125" in output
        assert "+0.500" in output

    def test_the_clamps_are_shown_as_percentages_not_log_ratios(self, output: str) -> None:
        """`log(0.5)` means "down 50%", which is what an operator can act on."""
        assert "[-50%, +100%]" in output

    def test_it_reports_the_row_count_that_was_stored(self, output: str) -> None:
        assert "wrote 548 target_price rows for mantra" in output

    def test_both_mover_lists_appear_with_their_headings(self, output: str) -> None:
        assert "Top 15 biggest UPWARD adjustments (target > qi):" in output
        assert "Top 15 biggest DOWNWARD adjustments (target < qi):" in output

    def test_a_mover_line_shows_the_move_and_the_reason(self, output: str) -> None:
        assert "Bumped               NAP  c       (MID    ) qi= 10 -> target= 30  flags=" in output
        assert "Cut                  NAP  c       (MID    ) qi= 30 -> target= 10  flags=floor_qi" in output

    def test_the_flag_counts_close_the_report(self, output: str) -> None:
        assert "Flag counts: {'floor_qi': 12}" in output.strip().splitlines()[-1]

    def test_a_flag_bearing_name_is_not_read_as_markup(self, output: str) -> None:
        """Mover lines print with `markup=False`. A flag is `team_discount(NAP)`, but a
        future one in brackets would otherwise be swallowed as a Rich tag."""
        import fantabot.interface.app as app

        console = Console(width=200, force_terminal=False, no_color=True)
        with console.capture() as captured:
            original, app.console = app.console, console
            try:
                render_pricing(
                    PricingReport(
                        system="classic", fades=(), team_factors={}, stored=0,
                        biggest_bumps=(_row("X", 1, 1, "[bold]not-markup[/bold]"),),
                        biggest_cuts=(), flag_counts={},
                    ),
                    top_n=1,
                )
            finally:
                app.console = original

        assert "[bold]not-markup[/bold]" in captured.get()
