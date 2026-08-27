from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from fantabot import state


@contextmanager
def context(headless: bool = True) -> Iterator[BrowserContext]:
    """Playwright context reusing a saved login session, if one was kept.

    The session file is opt-in now: `fantabot login` writes it only under
    `--save-session`, because as of 2026-08-26 nothing reads it. Both callers of
    this function — `lineup.py` and `auction.py` — raise `NotImplementedError`
    on their next line, so this path is not exercised by anything yet.
    """
    storage_state = state.storage_state_path()
    if not storage_state.exists():
        raise RuntimeError(
            f"No saved session at {storage_state}. Run "
            "`fantabot login --save-session` — but check first whether this code "
            "path needs cookies at all: as of 2026-08-26 nothing reads them."
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(storage_state=str(storage_state))
        try:
            yield ctx
        finally:
            ctx.close()
            browser.close()


@contextmanager
def interactive_login_context(channel: str | None = None) -> Iterator[BrowserContext]:
    """Headed context with no saved state — used only by the login commands.

    ``channel`` picks an installed browser (``"msedge"``, ``"chrome"``) instead
    of Playwright's bundled Chromium. It exists because Google refuses OAuth in
    a browser it considers automated — *"This browser or app may not be
    secure"*. Whether a channel helps is not obvious: the detection is about
    automation flags rather than the brand, so this is a cheap thing to try and
    not a fix to rely on. `fantalab-login --browser msedge`.

    **It no longer writes anything.** The caller decides, because it has to:
    `ctx.storage_state()` must be read *inside* the body, and this function used
    to write the file in its `finally`, on every login, whether or not anyone
    wanted it. That produced a plaintext file holding live cookies and every
    lega's bearer token, which — measured — nothing read.

    `login.py` now reads the state in the body and persists it only under
    `--save-session`.
    """
    with sync_playwright() as pw:
        launch: dict[str, object] = {"headless": False}
        if channel:
            launch["channel"] = channel
        browser = pw.chromium.launch(**launch)  # type: ignore[arg-type]
        ctx = browser.new_context()
        try:
            yield ctx
        finally:
            ctx.close()
            browser.close()
