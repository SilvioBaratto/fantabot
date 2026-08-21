import time

from playwright.sync_api import Page
from rich.console import Console

from fantabot import browser, state, strategy
from fantabot.config import settings
from fantabot.data_sources import StatsSource
from fantabot.models import AuctionListing, Role

console = Console()

POLL_INTERVAL_SECONDS = 5


def scrape_session_id(page: Page) -> str:
    """Identify the current auction session (e.g. "asta iniziale 2026/27" or
    "riparazione 1") so state.py can dedupe across restarts. TODO: real
    selector once the site's asta UI is inspected — see fantabot/CLAUDE.md.
    """
    raise NotImplementedError("scrape_session_id: DOM not mapped yet")


def scrape_current_listing(page: Page) -> AuctionListing | None:
    """Read the player currently up for bid, current price, and current
    bidder. Returns None when no player is actively up for auction right now
    (between listings, or session ended). TODO: real selectors.
    """
    raise NotImplementedError("scrape_current_listing: DOM not mapped yet")


def place_bid(page: Page, amount: int) -> None:
    """Click the bid button / submit the bid amount. TODO: real selectors."""
    raise NotImplementedError("place_bid: DOM not mapped yet")


def is_session_over(page: Page) -> bool:
    """TODO: real selector — e.g. "asta terminata" banner or listing absence
    persisting past a grace period."""
    raise NotImplementedError("is_session_over: DOM not mapped yet")


def watch_and_bid(
    stats_source: StatsSource,
    total_budget: int,
    role_share: dict[Role, float] | None = None,
    headless: bool = True,
) -> None:
    """Poll the live auction room and bid per strategy.decide_bid until the
    session ends. Designed to be started shortly before a scheduled asta
    (iniziale or riparazione) and left running for its duration — this is not
    a single cron tick, it's a long-lived loop (see README "Scheduling").
    """
    settings.require_credentials()
    role_budget = strategy.allocate_auction_budget(total_budget, role_share)
    bot_state = state.load()

    with browser.context(headless=headless) as ctx:
        page = ctx.new_page()
        page.goto(settings.lega_url)

        session_id = scrape_session_id(page)
        if bot_state.get("last_auction_session_id") != session_id:
            bot_state["last_auction_session_id"] = session_id
            bot_state["processed_bids"] = []
            state.save(bot_state)

        console.print(f"[bold]Watching auction session {session_id}[/bold]")
        while True:
            if is_session_over(page):
                console.print("[green]Auction session ended.[/green]")
                return

            listing = scrape_current_listing(page)
            if listing is not None:
                target_price = stats_source.target_price(listing.player)
                remaining = role_budget.get(listing.player.role, 0)
                decision = strategy.decide_bid(listing, target_price, remaining)
                if decision is not None:
                    console.print(f"{decision.player.name}: bid {decision.amount} — {decision.reasoning}")
                    if settings.fantabot_auto_act:
                        place_bid(page, decision.amount)
                        role_budget[listing.player.role] -= decision.amount
                    else:
                        console.print("[yellow]FANTABOT_AUTO_ACT=false — dry run, not bidding.[/yellow]")

            time.sleep(POLL_INTERVAL_SECONDS)
