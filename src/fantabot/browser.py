"""Playwright contexts. One of them, now: the headed window the login flows open.

``context()`` — a headless context that reused a saved ``storage_state.json`` — was
removed with its only two callers, ``lineup.py`` and ``auction.py``. Both were
unimplemented stubs that raised on the line after they opened it, so the path had
never run.
The saved-session file it depended on is still written under ``login --save-session``
and is still read by nothing; that is recorded at ``state.py``.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright


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
