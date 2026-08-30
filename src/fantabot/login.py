"""`fantabot login` — sign in once, store every lega's token encrypted.

Replaces the old `auth` command. Same headed browser, same manual sign-in; new
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
from typing import Any

from fantabot.interface.console import console
from fantabot.tokens.capture import CapturedToken, parse_storage_state
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.errors import KeyMissing, NoLeaguesFound, TokenError
from fantabot.tokens.status import TokenStatus

LOGIN_URL = "https://leghe.fantacalcio.it"
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


# `Any`, not `BrowserContext`: typing this with Playwright's class would put a
# Playwright import at module scope in a module `cli.py` imports, and
# `fantabot --help` must not load Playwright (T20 pins that). The fake browser in
# the tests is the same shape and satisfies the same call sites.
BrowserFactory = Callable[[], AbstractContextManager[Any]]


def _real_browser() -> AbstractContextManager[Any]:
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


def _read_blob(ctx: Any, prompt: Callable[[str], str]) -> list[CapturedToken]:
    """One `localStorage` read, with one explicit human-confirmed re-read.

    The likeliest real-world failure of this whole phase is pressing Enter
    before the Angular SPA has finished writing `LEAGUES2024_LOCAL` — a wasted
    password and captcha, for a timing race. So on a failed parse the human is
    *asked* to let it read again, rather than the code silently polling, which
    would contradict SPEC assumption 3's "one read" while claiming to honour it.

    Still zero clicks and zero selectors either way.
    """
    try:
        return parse_storage_state(dict(ctx.storage_state()))
    except NoLeaguesFound:
        console.print(
            "[yellow]LEAGUES2024_LOCAL not found — the page may still be "
            "loading.[/yellow]"
        )
        prompt("Press Enter to read again, or Ctrl-C to abort... ")
        return parse_storage_state(dict(ctx.storage_state()))


def _capture(
    *,
    cipher: TokenCipher,
    league: int,
    verify: bool,
    save_session: bool,
    browser_factory: BrowserFactory,
    transport: object | None,
    prompt: Callable[[str], str],
    moment: datetime,
) -> LoginResult:
    """The browser step, the parse, and the encrypted write."""
    from fantabot.db import database_manager
    from fantabot.tokens.store import TokenStore

    # The site root, deliberately — NOT `settings.lega_url`.
    #
    # Signing in does not need a lega-specific page, and `lega_url` is not
    # trustworthy for this: `.env.example` ships it as the placeholder
    # `.../nome-della-tua-lega`, which is non-empty, so an `or` fallback never
    # fires and the browser lands on a dead URL. Observed on a real run before
    # anyone had filled it in. `lega_url` is kept for the lega-specific pages a
    # future roster reader will need; nothing uses it today.
    console.print(f"\nOpening {LOGIN_URL} — log in, then press Enter here.")

    with browser_factory() as ctx:
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        prompt("Press Enter once you are logged in and can see your leghe... ")

        # Read INSIDE the body. After the context closes this call raises, and
        # the failure would only appear during a real login — which is why the
        # fake browser in the tests asserts on the ordering rather than trusting
        # it.
        captured = _read_blob(ctx, prompt)
        state_blob = dict(ctx.storage_state()) if save_session else None

    console.print(f"  read LEAGUES2024_LOCAL: {len(captured)} leghe")
    for one in captured:
        console.print(
            f"    {one.league_name or '—'} ({one.league_id})  l_id ok  "
            f"t_id {one.claims.team_id}  exp {one.claims.expires_at:%Y-%m-%d}"
        )

    if league and league not in {one.league_id for one in captured}:
        raise LoginAborted(
            f"--league {league} is not one of the leghe on this account "
            f"({', '.join(str(one.league_id) for one in captured)}). Nothing was stored.",
            code=1,
        )

    # `--league` narrows which rows get a NEW ciphertext. It never narrows which
    # leghe are stamped last_seen_at: every lega found in leagues[] was seen, and
    # without that split `login --league X` instantly and falsely reports the
    # other lega as ORPHANED. A wrong line of output, not a crash — which is
    # exactly the kind of bug that survives.
    to_store = [one for one in captured if not league or one.league_id == league]

    with database_manager.get_session() as session:
        store = TokenStore(session, cipher)
        store.save(to_store, now=moment)
        store.touch_seen([one.league_id for one in captured], moment)

    saved = _write_session(state_blob) if state_blob is not None else _warn_stale_session()

    verified, failures = (
        _verify([one.league_id for one in to_store], cipher, transport, moment)
        if verify
        else ([], [])
    )

    console.print(
        f"\n{len(to_store)} token(s) stored, {len(verified)} verified."
        + (f" {len(failures)} failed verification." if failures else "")
    )

    return LoginResult(
        stored=[one.league_id for one in to_store],
        verified=verified,
        failures=failures,
        browser_opened=True,
        session_saved=saved,
    )


def _verify(
    league_ids: list[int],
    cipher: TokenCipher,
    transport: object | None,
    moment: datetime,
) -> tuple[list[int], list[tuple[int, str]]]:
    """One GET per stored lega, proving the token authenticates headlessly.

    **A failure never rolls back the stored row.** The row is a credential we
    hold; whether the site liked it this second is a separate fact, which is
    exactly why `last_verified_at` is nullable rather than the row being
    conditional on it. A network blip must not cost you a token you just typed a
    password for.
    """
    import httpx

    from fantabot import apileague
    from fantabot.db import database_manager
    from fantabot.tokens.store import TokenStore

    console.print("\nVerifying:")
    verified: list[int] = []
    failures: list[tuple[int, str]] = []

    for league_id in league_ids:
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            try:
                body = apileague.league_status(
                    league_id,
                    store=store,
                    transport=transport if isinstance(transport, httpx.BaseTransport) else None,
                    now=moment,
                )
            except TokenError as exc:
                failures.append((league_id, str(exc)))
                console.print(f"  [yellow]{league_id}  {exc}[/yellow]")
                continue
            store.mark_verified(league_id, moment)

        verified.append(league_id)
        console.print(
            f"  {league_id}  GET /onboarding/v1/league/status  200  "
            f"sId={body.get('sId')} mday={body.get('mday')}"
        )

    return verified, failures


def _write_session(blob: dict[str, Any]) -> bool:
    import json

    from fantabot import state
    from fantabot.config import settings

    settings.fantabot_data_dir.mkdir(parents=True, exist_ok=True)
    path = state.storage_state_path()
    path.write_text(json.dumps(blob))
    console.print(f"  session -> {path}")
    return True


def _warn_stale_session() -> bool:
    """Say something about a leftover file; never delete it.

    Removing user data is on SPEC's Ask-first list, and a file somebody kept on
    purpose is not ours to tidy away.
    """
    from fantabot import state

    path = state.storage_state_path()
    if path.exists():
        console.print(
            f"[yellow]  {path} already exists and was left untouched. Nothing "
            "reads it as of 2026-08-26; delete it yourself if you no longer want "
            "a plaintext session on disk.[/yellow]"
        )
    else:
        console.print(
            "  session not saved (nothing reads it — pass --save-session if you "
            "need cookies)"
        )
    return False


__all__ = ["LoginAborted", "LoginResult", "run"]
