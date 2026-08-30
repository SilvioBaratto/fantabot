"""Run-level usage totals and the cost report line. Pure and synchronous.

``total_usage`` folds the per-player usage the fan-out collected into one figure;
``format_cost_line`` turns it into the single stdout line ``news fetch`` prints at
the end of a run. The dollar estimate is deliberately hedged — on a custom Foundry
model id the SDK's price table may not recognise the model and reports 0 — so the
load-bearing numbers are the token counts and the cache-read fraction.
"""

from __future__ import annotations

from fantabot.adapters.agent.runner import Usage
from fantabot.news.pipeline import PlayerOutcome, format_cost_line, total_usage
from fantabot.news.pool import PoolPlayer


def _player(name: str = "X") -> PoolPlayer:
    return PoolPlayer(id="1", nome=name, squadra="T", ruolo="A", ruoli_mantra="A")


def _out(
    usage: Usage,
    *,
    row: dict[str, str] | None = None,
    failure: str | None = None,
    skipped: bool = False,
) -> PlayerOutcome:
    return PlayerOutcome(
        player=_player(),
        row=row,
        failure=failure,
        rate_limited=False,
        skipped=skipped,
        usage=usage,
    )


def test_total_usage_sums_including_failed_and_skipped() -> None:
    # A failed or skipped player may still have spent web searches before it
    # ended, so its tokens count toward the run's cost.
    outcomes = [
        _out(Usage(input_tokens=10, cache_read_tokens=90, cost_usd=0.01), row={"a": "b"}),
        _out(Usage(input_tokens=5, cost_usd=0.002), failure="boom"),
        _out(Usage(input_tokens=1), skipped=True),
    ]
    u = total_usage(outcomes)
    assert u.input_tokens == 16
    assert u.cache_read_tokens == 90
    assert u.cost_usd == 0.012


def test_format_cost_line_reports_cache_read_fraction() -> None:
    u = Usage(input_tokens=100, output_tokens=50, cache_read_tokens=900, cache_creation_tokens=0, cost_usd=0.01)
    line = format_cost_line(u)
    assert "900" in line          # cache-read tokens surfaced
    assert "90%" in line          # 900 / (100 + 0 + 900)
    assert "approx" in line       # the dollar hedge is always present


def test_format_cost_line_handles_zero_without_dividing() -> None:
    line = format_cost_line(Usage())
    assert "0%" in line           # no ZeroDivisionError on an empty run
