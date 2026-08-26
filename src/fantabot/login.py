"""`fantabot login` — sign in once, store every lega's token encrypted.

Replaces `fantabot auth`. Same headed browser, same manual sign-in; what is new
is that the bearer token goes into Postgres encrypted instead of sitting in a
plaintext file, and that `storage_state.json` is written only on request.

**Everything checkable is checked before the browser opens.** A password typed
into a real browser, possibly with a captcha, is expensive to waste — and this
preflight is what replaced the old write-the-file-first safety net (SPEC
assumption 5). Nothing here is a round-trip: the key is validated by
constructing the cipher, and the database by the `SELECT 1` `db-check` already
uses.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console

from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.errors import KeyMissing, TokenError
from fantabot.tokens.status import TokenStatus

console = Console()

EXIT_PREFLIGHT = 2


class LoginAborted(Exception):
    """A preflight refused. Carries the exit code the command should use."""

    def __init__(self, message: str, code: int = EXIT_PREFLIGHT) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LoginResult:
    """What one run did, so the command can report without re-deriving it."""

    stored: list[int]
    verified: list[int]
    failures: list[tuple[int, str]]
    browser_opened: bool
    session_saved: bool


def _preflight_key() -> TokenCipher:
    """The key, or a refusal — before anything expensive happens.

    `KeyMissing` and `KeyMalformed` are different sentences on purpose, and
    `TokenCipher` distinguishes them by checking emptiness *before* constructing
    `Fernet`: `Fernet("")` and `Fernet("not-a-key")` both raise `ValueError`, so
    a naive implementation tells you to fix the shape of a key you never set.
    """
    from fantabot.config import settings

    try:
        return TokenCipher(settings.fantabot_encryption_key)
    except KeyMissing as exc:
        raise LoginAborted(str(exc)) from None
    except TokenError as exc:
        raise LoginAborted(str(exc)) from None


def _preflight_database() -> None:
    """A `SELECT 1`, worded as `db-check` words it. The DSN is masked."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.config import settings
    from fantabot.db import database_manager

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


BrowserFactory = Callable[[], AbstractContextManager[object]]


def _real_browser() -> AbstractContextManager[object]:
    from fantabot import browser

    return browser.interactive_login_context()


def _prompt(message: str) -> str:
    return input(message)


def run(
    *,
    league: int = 0,
    force: bool = False,
    verify: bool = True,
    save_session: bool = False,
    browser_factory: BrowserFactory | None = None,
    transport: object | None = None,
    prompt: Callable[[str], str] = _prompt,
    now: datetime | None = None,
) -> LoginResult:
    """One login. Injected collaborators so the decision table is testable.

    `browser_factory` and `transport` default to the real ones; the tests pass
    fakes, which is the only reason `tests/test_cli_login.py` does not launch
    Chromium and open a socket.
    """
    from fantabot.db import database_manager
    from fantabot.tokens.store import TokenStore

    moment = now or datetime.now(UTC)

    cipher = _preflight_key()
    console.print(f"Encryption key: [green]ok[/green] (fingerprint {cipher.fingerprint})")
    _preflight_database()
    console.print("Database:       [green]ok[/green]")

    with database_manager.get_session() as session:
        existing = TokenStore(session, cipher).status()

    if not force and _all_valid(existing, moment, league):
        summary = ", ".join(
            f"{row.league_id} ({(row.expires_at - moment).days}d)" for row in existing
        )
        console.print(
            f"All stored tokens valid — {summary}. No browser opened.\n"
            "Force a re-auth with --force."
        )
        return LoginResult([], [], [], browser_opened=False, session_saved=False)

    return _capture(
        cipher=cipher,
        league=league,
        verify=verify,
        save_session=save_session,
        browser_factory=browser_factory or _real_browser,
        transport=transport,
        prompt=prompt,
        moment=moment,
    )


def _all_valid(rows: Sequence[TokenStatus], moment: datetime, league: int) -> bool:
    """Every lega we would act on already has a live token.

    `Sequence`, not `list`: `list` is invariant, so a `list[TokenStatus]` from
    the store would not satisfy a `list[object]` parameter under strict mode.
    """
    if not rows:
        return False
    wanted = [row for row in rows if not league or row.league_id == league]
    return bool(wanted) and all(moment < row.expires_at for row in wanted)


def _capture(**_: object) -> LoginResult:  # pragma: no cover - filled in at T18
    raise NotImplementedError("the browser step lands in T18")


__all__ = ["LoginAborted", "LoginResult", "run"]
