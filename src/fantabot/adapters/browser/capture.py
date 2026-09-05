"""Playwright contexts. One of them, now: the headed window the login flows open.

``context()`` — a headless context that reused a saved ``storage_state.json`` — was
removed with its only two callers, ``lineup.py`` and ``auction.py``. Both were
unimplemented stubs that raised on the line after they opened it, so the path had
never run.
The saved-session file it depended on is still written under ``login --save-session``
and is still read by nothing; that is recorded at ``state.py``.
"""

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from playwright.sync_api import BrowserContext, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from fantabot.domain.tokens.errors import SignInWindowClosed, StorageReadFailed


@contextmanager
def interactive_login_context(channel: str | None = None) -> Iterator[BrowserContext]:
    """Headed context with no saved state — used only by the login commands.

    ``channel`` picks an installed browser (``"msedge"``, ``"chrome"``) instead
    of Playwright's bundled Chromium. It exists because Google refuses OAuth in
    a browser it considers automated — *"This browser or app may not be
    secure"*. Whether a channel helps is not obvious: the detection is about
    automation flags rather than the brand, so this is a cheap thing to try and
    not a fix to rely on. `auth fantalab-login --browser msedge`.

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


def real_browser(channel: str | None = None) -> AbstractContextManager[BrowserContext]:
    """A `BrowserFactory` over the real thing, for the interface to inject.

    It lived in `application/auth_login.py` and `application/fantalab_login.py` as a
    private `_real_browser` default, with the Playwright import inside the function body
    so that `fantabot --help` would not load it. That kept the *cost* out of the import
    path but not the dependency: both use cases named the browser package, and the
    application layer is meant to reach the outside world only through a port it is
    handed. The `browser_factory` seam already existed and the tests already used it;
    only the default was pointing the wrong way.
    """
    return interactive_login_context(channel)


def read_storage_state(ctx: BrowserContext) -> Mapping[str, Any]:
    """One read of the browser's storage, for the capture loop to poll.

    Two things this must not do, both of which have bitten before.

    **Never `path=`.** That form json.dumps the whole state to disk, which for
    FantaLab means `refresh_token`, `id_token` and `access_token` in cleartext in a
    file. The values go from browser memory to Fernet to Postgres with no plaintext
    stop in between.

    **Translate the closed window here.** A human shutting the browser is the one
    terminal condition the loop must not sit out, and Playwright reports it as
    `TargetClosedError` — which 1.62 does not export from `playwright.sync_api`
    (only `Error`, `TimeoutError`, `WebError`). Hence the name check rather than an
    import that would break on a version bump. The translation lives in the adapter
    because `application/` may not import playwright at all.
    """
    try:
        return dict(ctx.storage_state())
    except PlaywrightError as exc:
        if type(exc).__name__ == "TargetClosedError":
            raise SignInWindowClosed() from None
        # Anything else is the collector, not the credential: the whole call aborts if
        # a single visited origin fails to load, which on an ad-funded site is a
        # routine event rather than a reason to abandon a login. Message dropped
        # deliberately — Playwright's call log names every origin it walked.
        raise StorageReadFailed() from None
