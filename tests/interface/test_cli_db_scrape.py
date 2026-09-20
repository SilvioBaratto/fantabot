"""T23: `db scrape` is a printer over one application function.

The lift's own proof. The body held which tables are scrapable, which module each is,
and what "no `--season`" means — three things the app had to guess at or reimplement —
and validated nothing else, so a typo'd season became 38 giornate of 404s and retries.

It also gained the one thing the terminal never said out loud: which seasons a run with
no `--season` actually took. `voti` and `statistiche` default to a list that stops at
2025/26, so that run scrapes last season and reports success. Now it prints the list and
warns when the list misses the season being played.

No socket: `run_scrape` is patched at the point the command imports it, which is also
where a body that grew its own `import_module` back would stop being patched and fail here.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fantabot.application import scrape as scrape_module
from fantabot.application.scrape import ScrapeRequest
from fantabot.interface.app import app

runner = CliRunner()


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[ScrapeRequest]:
    """Every request that reached `run_scrape`. Empty means nothing was fetched."""
    calls: list[ScrapeRequest] = []
    monkeypatch.setattr(scrape_module, "run_scrape", calls.append)
    return calls


@pytest.fixture
def season_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the season being played, so the warning is not a coin flip each August."""
    monkeypatch.setattr(scrape_module, "current_season", lambda _today: "2026/27")


def test_an_unknown_table_is_refused_and_nothing_is_fetched(ran: list[ScrapeRequest]) -> None:
    result = runner.invoke(app, ["db", "scrape", "fixtures"])
    assert result.exit_code == 2
    assert "quotazioni" in result.output and "voti" in result.output
    assert ran == []


def test_a_malformed_season_is_refused_before_the_first_request(
    ran: list[ScrapeRequest],
) -> None:
    result = runner.invoke(app, ["db", "scrape", "voti", "--season", "2022/26"])
    assert result.exit_code == 2
    assert ran == []


def test_the_seasons_it_will_fetch_are_printed(
    ran: list[ScrapeRequest], season_now: None
) -> None:
    result = runner.invoke(app, ["db", "scrape", "voti", "--season", "2026/27"])
    assert result.exit_code == 0
    assert "2026/27" in result.output
    assert [r.seasons for r in ran] == [("2026/27",)]


def test_a_run_with_no_season_says_which_four_it_took(
    ran: list[ScrapeRequest], season_now: None
) -> None:
    from fantabot.adapters.scraping import voti

    result = runner.invoke(app, ["db", "scrape", "voti"])
    assert result.exit_code == 0
    for season in voti.DEFAULT_SEASONS:
        assert season in result.output
    assert [r.seasons for r in ran] == [tuple(voti.DEFAULT_SEASONS)]


def test_a_default_that_misses_the_season_being_played_warns(
    ran: list[ScrapeRequest], season_now: None
) -> None:
    """The trap, said out loud: the run still happens, and reports success, and is wrong."""
    result = runner.invoke(app, ["db", "scrape", "voti"])
    assert "2026/27" in result.output
    assert "--season" in result.output
    assert ran != []


def test_naming_the_season_does_not_warn(ran: list[ScrapeRequest], season_now: None) -> None:
    result = runner.invoke(app, ["db", "scrape", "voti", "--season", "2026/27"])
    assert "--season 2026/27" not in result.output


def test_a_table_whose_default_is_current_does_not_warn(
    ran: list[ScrapeRequest], season_now: None
) -> None:
    """`quotazioni` already carries 2026/27, so the same bare run is not the trap."""
    result = runner.invoke(app, ["db", "scrape", "quotazioni"])
    assert result.exit_code == 0
    assert "--season 2026/27" not in result.output
