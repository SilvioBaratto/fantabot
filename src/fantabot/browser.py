from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from fantabot import state
from fantabot.config import settings


@contextmanager
def context(headless: bool = True) -> Iterator[BrowserContext]:
    """Playwright context reusing the saved login session.

    Raises if no storage state exists yet — run `fantabot auth` first.
    """
    storage_state = state.storage_state_path()
    if not storage_state.exists():
        raise RuntimeError("No saved session. Run `fantabot auth` first.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(storage_state=str(storage_state))
        try:
            yield ctx
        finally:
            ctx.close()
            browser.close()


@contextmanager
def interactive_login_context() -> Iterator[BrowserContext]:
    """Headed context with no saved state — used only by `fantabot auth`."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context()
        try:
            yield ctx
        finally:
            settings.fantabot_data_dir.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(state.storage_state_path()))
            ctx.close()
            browser.close()
