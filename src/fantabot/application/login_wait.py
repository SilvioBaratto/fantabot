"""Reading the credential once the human says they are done.

The login flows open a real browser, the human signs in, and the credential lands
in `localStorage`. Something has to decide *when* to read it, and this module runs
from the moment the human says so until the credential is fully there.

**Why the human still gives the signal.** Detecting sign-in automatically was built
and reverted, with the evidence in hand. `BrowserContext.storage_state()` collects
every origin the context has visited, opening a temporary page and navigating to
each one that has no page of its own. On leghe.fantacalcio.it the ad and analytics
iframes load, detach, and leave their origins behind — so each read opened a burst
of tabs over the login form, once every two seconds, and made signing in
impossible. Measured live: `net::ERR_ABORTED` while navigating to
`safeframe.googlesyndication.com`, after ~240 reads.

The other mechanisms are worse, not better. Observing a `localStorage` write needs
script evaluated in the page, at every level including CDP's `DOMStorage` (which
takes a `page` to open a session against). `BrowserContext.on(...)` deadlocks under
the sync API — its callbacks dispatch only while the main greenlet is inside a
Playwright call, so `on(...)` + `Event` + `sleep()` never wakes. Suppressing the ad
origins would mean intercepting the site's own network traffic.

So the human presses a button, and this module does the rest: retrying a read that
the collector aborts, and holding a half-written credential until it settles.

**What "still zero clicks" now means, precisely.** The read is a context-channel
call, so the page object still receives exactly one call — `goto` — and the tests
that pin `ctx.page.calls == ["goto"]` still pass. It is not, however, literally
untouched machinery: `storage_state()` evaluates a collector script in Chromium's
*isolated utility world*, invisible to the site's own JavaScript, and may open a
temporary blank page for an origin no open page holds. The guarantee is that this
program never clicks, types into, or navigates the login page — not that no script
runs anywhere in the browser.

**Nothing here may print anything derived from what it read.** This loop holds a
credential several times per confirmation. Its output is a constant string, never a
length, a prefix, or a boolean about a value.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from fantabot.application.reporting import Reporter
from fantabot.domain.tokens.errors import SignInWindowClosed, StorageReadFailed

#: Two seconds. Each read can open and navigate a temporary tab per visited origin,
#: so reads are worth spacing out even though there are now only a handful of them.
POLL_INTERVAL_S = 2.0

#: Sixty seconds. This runs *after* the human has said they are signed in, so the
#: credential is either already written or a few SPA ticks away; ten minutes would
#: only mean ten minutes of tab-opening reads before reporting the same failure.
DEADLINE_S = 60.0

#: Consecutive reads that must agree before an *incomplete* credential is accepted.
#: See `await_capture`.
SETTLE_TICKS = 5

#: Consecutive failed *reads* tolerated before giving up. A single third-party origin
#: failing to load aborts the whole collection, which is routine; five in a row is not.
READ_FAILURE_BUDGET = 5

#: How many times the human may confirm before a flow gives up. Generous, because each
#: confirmation costs a single read — the old behaviour spent a minute re-reading and
#: then killed the login, so an early click meant starting over with a fresh browser.
MAX_CONFIRMS = 10

T = TypeVar("T")


class CaptureUnreadable(Exception):
    """The browser's storage could not be read, repeatedly.

    Separate from `CredentialNotThere` because it means something different: the
    credential may well be there, but the collector keeps aborting.
    """

    def __init__(self, attempts: int) -> None:
        super().__init__(
            f"could not read the browser's storage on {attempts} consecutive attempts. "
            "Nothing was written. Close any extra tabs in the sign-in window and try "
            "again."
        )


class CredentialNotThere(Exception):
    """Confirmed, but the credential is not in the browser yet. **Recoverable.**

    Raised on the first failed parse rather than after a retry loop, and that is the
    whole point. Each read makes Playwright walk every origin the context has visited,
    opening and navigating a temporary page for any that has none — on an ad-funded
    site that is a burst of tabs across the login form. Retrying for a minute meant
    thirty such bursts over the page the human was still trying to use, and then killing
    the job so they had to start again with a fresh browser.

    So confirming too early costs exactly one read, and the caller asks again.
    """

    def __init__(self) -> None:
        super().__init__("the credential is not in the browser yet")


def read_credential(
    read_state: Callable[[], Mapping[str, Any]],
    parse: Callable[[Mapping[str, Any]], T],
    *,
    is_complete: Callable[[T], bool],
    not_ready: tuple[type[Exception], ...],
    report: Reporter,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """One confirmed read of the credential. Cheap when it is not there.

    `not_ready` names the exceptions that mean "the browser has not written it" —
    `NoLeaguesFound` for a lega, a bare `TokenError` for FantaLab. Those raise
    `CredentialNotThere` at once, so the caller can ask the human to confirm again.
    Anything else propagates: `parse_storage_state` raises `LeagueMismatch` when the
    blob is present and decodes to a different lega, and no amount of asking fixes that.

    `is_complete` is why this can still loop. A parse can succeed on a credential the
    browser has not finished writing — `parse_fantalab_storage` requires `refresh_token`
    and `user_id` but treats `id_token` and `access_token` as optional — so an
    incomplete one is held until `SETTLE_TICKS` consecutive reads agree. That path only
    runs while the credential is actively arriving, which is the one moment extra reads
    are worth their cost.

    A read that fails outright is the collector, not the credential: one flaky
    third-party origin aborts the whole collection, so it is retried up to
    `READ_FAILURE_BUDGET` consecutive times.
    """
    failures = 0
    held = 0
    settled: T | None = None

    while True:
        try:
            parsed = parse(read_state())
        except SignInWindowClosed:
            raise
        except StorageReadFailed:
            failures += 1
            if failures >= READ_FAILURE_BUDGET:
                raise CaptureUnreadable(failures) from None
            sleep(POLL_INTERVAL_S)
            continue
        except not_ready:
            raise CredentialNotThere() from None

        failures = 0
        if is_complete(parsed):
            return parsed

        held += 1
        settled = parsed
        if held >= SETTLE_TICKS:
            report.print(
                "  the browser stopped writing before every optional field appeared "
                "— storing what is there."
            )
            return settled
        sleep(POLL_INTERVAL_S)
