"""Watch a live auction room and bid.

Budget is persisted rather than counted in memory. ``role_budget`` used to be a
local dict decremented at each bid, so a crash mid-asta lost every spend and the
next start handed the bot back its full credits — with players already bought.
Each bid is now written to ``auction_bids`` **before** it is placed, and
remaining budget is derived from those rows.

Write-ahead rather than write-after, deliberately. A crash between the write and
the bid reserves credits that were never spent, which is conservative; a crash
the other way round loses a spend that did happen, and the bot re-spends money
it no longer has.

**Bids are recorded as ``pending`` and nothing settles them yet.** Settling needs
the room's result, which needs the DOM that ``scrape_current_listing`` and
``is_session_over`` still do not map. Pending bids reserve their full amount, so
the budget errs low until then. ``AuctionRepository.settle_bid`` is ready for the
call site that does not exist.
"""

import time
from datetime import UTC, datetime

from playwright.sync_api import Page
from rich.console import Console

from fantabot import browser, strategy
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
    from fantabot.db import database_manager
    from fantabot.db.repositories.runtime import AuctionRepository, RuntimeRepository

    settings.require_credentials()
    league_id = settings.fantabot_league_id
    allocation = {
        role.value: credits
        for role, credits in strategy.allocate_auction_budget(
            total_budget, role_share
        ).items()
    }

    with browser.context(headless=headless) as ctx:
        page = ctx.new_page()
        page.goto(settings.lega_url)

        session_id = scrape_session_id(page)
        with database_manager.get_session() as session:
            RuntimeRepository(session).record_auction_session(league_id, session_id)

        console.print(f"[bold]Watching auction session {session_id}[/bold]")
        while True:
            if is_session_over(page):
                console.print("[green]Auction session ended.[/green]")
                return

            listing = scrape_current_listing(page)
            if listing is not None:
                target_price = stats_source.target_price(listing.player)
                # Re-read every poll rather than tracking a counter: this is what
                # makes a restart cost nothing, and it costs one indexed query.
                with database_manager.get_session() as session:
                    remaining_by_role = AuctionRepository(session).remaining_budget(
                        league_id, session_id, allocation
                    )
                remaining = remaining_by_role.get(listing.player.role.value, 0)
                decision = strategy.decide_bid(listing, target_price, remaining)
                if decision is not None:
                    console.print(
                        f"{decision.player.name}: bid {decision.amount} — {decision.reasoning}"
                    )
                    if settings.fantabot_auto_act:
                        # Written first: a crash after this and before place_bid
                        # reserves credits that were never spent, which is the
                        # safe direction. The reverse loses a real spend.
                        with database_manager.get_session() as session:
                            AuctionRepository(session).record_bid(
                                league_id=league_id,
                                session_id=session_id,
                                player_id=int(decision.player.id),
                                role=listing.player.role.value,
                                amount=decision.amount,
                                placed_at=datetime.now(UTC),
                            )
                        place_bid(page, decision.amount)
                    else:
                        console.print(
                            "[yellow]FANTABOT_AUTO_ACT=false — dry run, not bidding.[/yellow]"
                        )

            time.sleep(POLL_INTERVAL_SECONDS)
