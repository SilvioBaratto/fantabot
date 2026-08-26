from playwright.sync_api import Page
from rich.console import Console

from fantabot import browser, strategy
from fantabot.config import settings
from fantabot.data_sources import StatsSource
from fantabot.models import Lineup, MatchdayInfo, RosterSlot

console = Console()


def scrape_matchday_info(page: Page) -> MatchdayInfo:
    """Read the current matchday number + lineup-lock deadline from the league
    home page. TODO: fill in real selectors once the site's DOM is inspected
    (see fantabot/CLAUDE.md "Known unknowns").
    """
    raise NotImplementedError(
        "scrape_matchday_info: leghe.fantacalcio.it DOM not mapped yet — "
        "run `fantabot auth`, open the roster page, and record the selectors."
    )


def scrape_roster(page: Page, matchday: int) -> list[RosterSlot]:
    """Read owned players + per-matchday availability/lineup-eligibility from
    the "Formazione" page. TODO: real selectors, see scrape_matchday_info.
    """
    raise NotImplementedError("scrape_roster: DOM not mapped yet")


def submit_lineup(page: Page, lineup: Lineup) -> None:
    """Click the formation + starters + captain into the site's lineup form
    and confirm. TODO: real selectors, see scrape_matchday_info.
    """
    raise NotImplementedError("submit_lineup: DOM not mapped yet")


def run_once(stats_source: StatsSource, headless: bool = True) -> None:
    """Submit one matchday's lineup, unless this lega already has it.

    The no-resubmit guard is a database read now, and it is scoped to
    ``FANTABOT_LEAGUE_ID``: the account is in two leghe, and the flat file this
    replaces had one matchday for both, so submitting in one marked the other
    done.
    """
    from fantabot.db import database_manager
    from fantabot.db.repositories.runtime import RuntimeRepository

    settings.require_credentials()
    league_id = settings.fantabot_league_id

    with browser.context(headless=headless) as ctx:
        page = ctx.new_page()
        page.goto(settings.lega_url)

        info = scrape_matchday_info(page)
        with database_manager.get_session() as session:
            already = RuntimeRepository(session).last_lineup_matchday(league_id)
        if already == info.matchday:
            console.print(f"[yellow]Matchday {info.matchday} already submitted, skipping.[/yellow]")
            return

        roster = scrape_roster(page, info.matchday)
        scores = stats_source.projected_scores(info.matchday)
        scored_roster = [
            RosterSlot(player=r.player, scored=scores.get(r.player.id, r.scored)) for r in roster
        ]

        lineup = strategy.pick_starting_lineup(scored_roster)
        console.print(
            f"Matchday {info.matchday}: {lineup.formation} — captain {lineup.captain.name}"
        )

        if not settings.fantabot_auto_act:
            console.print("[yellow]FANTABOT_AUTO_ACT=false — dry run, not submitting.[/yellow]")
            return

        submit_lineup(page, lineup)
        # Recorded only after the submit returns. Marking it first would make a
        # failed submit look done and skip the matchday for good.
        with database_manager.get_session() as session:
            RuntimeRepository(session).record_lineup_submitted(league_id, info.matchday)
        console.print("[green]Lineup submitted.[/green]")
