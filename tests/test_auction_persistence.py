"""auction.py's budget is persisted, not counted in memory.

watch_and_bid cannot be run: four DOM stubs still raise, and SPEC's Non-goals
fence them off. So the properties pinned here are the ones that would be silent
if they regressed — and every one of them is about money.
"""

from __future__ import annotations

from pathlib import Path

SOURCE = Path("src/fantabot/auction.py").read_text()


def test_the_in_memory_counter_is_gone() -> None:
    """role_budget[...] -= amount was the whole bug: a crash mid-asta lost every
    spend and the next start handed back full credits, with players bought."""
    assert "role_budget[" not in SOURCE
    assert "-=" not in SOURCE


def test_the_budget_is_read_from_the_repository_each_poll() -> None:
    """Re-reading is what makes a restart cost nothing."""
    assert "remaining_budget(" in SOURCE


def test_the_bid_is_written_before_it_is_placed() -> None:
    """Write-ahead. A crash between the write and the bid reserves credits that
    were never spent — conservative. The reverse loses a spend that did happen,
    and the bot re-spends money it no longer has."""
    record_at = SOURCE.index("record_bid(")
    place_at = SOURCE.index("place_bid(page, decision.amount)")

    assert record_at < place_at


def test_nothing_is_written_when_auto_act_is_off() -> None:
    """The dry run must stay a dry run: reserving credits for a bid that was
    never placed would shrink a real budget on a rehearsal."""
    guard_at = SOURCE.index("if settings.fantabot_auto_act:")
    record_at = SOURCE.index("record_bid(")
    dry_run_at = SOURCE.index("FANTABOT_AUTO_ACT=false")

    assert guard_at < record_at < dry_run_at


def test_the_flat_state_file_is_no_longer_used() -> None:
    assert "state.load()" not in SOURCE
    assert "state.save(" not in SOURCE
    assert "processed_bids" not in SOURCE


def test_the_dom_stubs_are_untouched() -> None:
    """SPEC's Non-goals: this task persists state, it does not map the DOM."""
    for stub in (
        "scrape_session_id",
        "scrape_current_listing",
        "place_bid",
        "is_session_over",
    ):
        assert f"def {stub}(" in SOURCE
    assert SOURCE.count("NotImplementedError") == 4


def test_settling_is_documented_as_not_wired_yet() -> None:
    """Bids stay pending until the room's result can be read, so the budget errs
    low. Silence here would look like an oversight rather than a decision."""
    assert "pending" in SOURCE
    assert "settle_bid" in SOURCE
