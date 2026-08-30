"""`fantabot auth fantalab-login` — sign in once, store the session encrypted.

Same posture as `login.py`, and for the same reason: **the sign-in is never
scripted and no page is ever clicked.** A scripted credential entry is what gets
accounts flagged, and FantaLab offers Google and Apple sign-in besides its own
form, so there is no single flow to automate even if we wanted one.

What is new here is what must *not* happen. Spike S3 established that FantaLab
keeps `refresh_token`, `id_token` and `access_token` in `localStorage`. That is
what makes a capture possible at all — Playwright's `storage_state` reads
localStorage and not IndexedDB — but it also means writing that state to a file
would put three credentials on disk in the clear. So no `storage_state.json` is
produced, not even a git-ignored one: the values go from browser memory through
Fernet into Postgres with no plaintext stop in between.

Everything checkable is checked before the browser opens. A password typed into
a real browser is expensive to waste on a missing key or a stopped database.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fantabot.interface.console import console
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.errors import KeyMissing, TokenError
from fantabot.tokens.fantalab import FantalabSession, parse_fantalab_storage

LOGIN_URL = "https://app.fantalab.it/aste-live"
EXIT_PREFLIGHT = 2


class LoginAborted(Exception):
    """A preflight refused. Carries the exit code the command should use."""

    def __init__(self, message: str, code: int = EXIT_PREFLIGHT) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FantalabLoginResult:
    """What one run did. Deliberately carries no credential."""

    user_id: str | None
    browser_opened: bool
    stored: bool


BrowserFactory = Callable[[], AbstractContextManager[Any]]


def _real_browser(channel: str | None = None) -> AbstractContextManager[Any]:
    # Imported here, not at module scope: `fantabot --help` must not load
    # Playwright, and a test pins that.
    from fantabot import browser

    return browser.interactive_login_context(channel)


def _prompt(message: str) -> str:
    return input(message)


def _preflight_key() -> TokenCipher:
    from fantabot.config import settings

    try:
        return TokenCipher(settings.fantabot_encryption_key)
    except KeyMissing as exc:
        raise LoginAborted(str(exc)) from None
    except TokenError as exc:
        raise LoginAborted(str(exc)) from None


def _preflight_database() -> None:
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.config import settings

    try:
        with database_manager.get_session() as session:
            session.execute(text("SELECT 1")).fetchone()
    except SQLAlchemyError as exc:
        dsn = make_url(settings.fantabot_database_url).render_as_string(hide_password=True)
        raise LoginAborted(
            f"Cannot reach the database at {dsn}\n"
            f"{type(exc).__name__}: {str(exc).splitlines()[0]}\n"
            "Start it with: docker compose up -d\n"
            "Nothing was opened and nothing was written."
        ) from None


def _read_session(ctx: Any, prompt: Callable[[str], str]) -> FantalabSession:
    """One `storage_state` read, with one human-confirmed retry.

    The likeliest failure is pressing Enter before the SPA has finished writing
    its tokens — a wasted sign-in for a timing race. So on a failed parse the
    human is *asked* to let it read again, rather than the code silently
    polling, which would contradict the one-read rule while claiming to honour it.

    Still zero clicks and zero selectors either way.
    """
    try:
        return parse_fantalab_storage(dict(ctx.storage_state()))
    except TokenError as first:
        console.print(f"[yellow]{first}[/yellow]")
        prompt("Press Enter to read again, or Ctrl-C to abort... ")
        return parse_fantalab_storage(dict(ctx.storage_state()))


def run(
    *,
    force: bool = False,
    channel: str | None = None,
    browser_factory: BrowserFactory | None = None,
    prompt: Callable[[str], str] = _prompt,
    now: datetime | None = None,
) -> FantalabLoginResult:
    """One login. Collaborators are injected so the flow is testable with fakes."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.tokens.fantalab_store import FantalabStore

    moment = now or datetime.now(UTC)

    cipher = _preflight_key()
    console.print(f"Encryption key: [green]ok[/green] (fingerprint {cipher.fingerprint})")
    _preflight_database()
    console.print("Database:       [green]ok[/green]")

    if not force:
        with database_manager.get_session() as session:
            existing = FantalabStore(session, cipher).describe()
        if existing:
            user_id, captured_at, _ = existing[0]
            console.print(
                f"A session is already stored for {user_id} "
                f"(captured {captured_at:%Y-%m-%d %H:%M}). No browser opened.\n"
                "Force a re-auth with --force."
            )
            return FantalabLoginResult(user_id, browser_opened=False, stored=False)

    console.print(
        f"\nOpening a browser at {LOGIN_URL}.\n"
        "Sign in yourself — this program types nothing and clicks nothing.\n"
        "When the auction list has finished loading, come back here."
    )

    factory = browser_factory or (lambda: _real_browser(channel))
    with factory() as ctx:
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        prompt("\nPress Enter once you are signed in and the page has loaded... ")
        captured = _read_session(ctx, prompt)

    with database_manager.get_session() as session:
        FantalabStore(session, cipher).save(captured, now=moment)
        session.commit()

    # The user id, and nothing else. Not the token, not a prefix of it, not its
    # length — CLAUDE.md's rule admits no truncated form.
    console.print(f"\n[green]Session stored, encrypted, for {captured.user_id}[/green]")
    console.print("No storage_state.json was written.")
    return FantalabLoginResult(captured.user_id, browser_opened=True, stored=True)
