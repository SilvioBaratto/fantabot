"""Everything this phase can raise, and what each one tells you to do.

One family, one place to look. `login.py` catches `TokenError` and reports;
nothing else has to know the taxonomy. The transport-shaped errors live here
too, rather than in `apileague.py`, because "what can go wrong with the token"
does not split cleanly at the HTTP boundary — a `401 ATH001` *is* a token
problem, and the answer to it is the same command as for an expired one.

**No error takes a token, a plaintext or a ciphertext as an argument.** An
exception carrying a credential is a credential in every traceback that touches
it, and tracebacks reach pytest output and cron logs. `tests/test_token_secrecy.py`
walks `Raise` nodes for exactly this.

Every message names the command that fixes the situation, in the style of
`cli.py`'s `docker compose up -d`. An error that says what broke but not what to
do is a puzzle, not a diagnostic.

**Naming ruling.** SPEC's Project Structure lists `KeyMismatch`, while SPEC's own
executable Code Style snippet raises `TokenUndecryptable` for both the
wrong-key case and the corrupt-row case, and SC 15 constrains only the message.
This file follows the snippet — the executable form wins over the prose list.
Recorded here so it does not surface as a review comment after four modules have
imported the chosen name.
"""

from __future__ import annotations

GENERATE_KEY_HINT = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)
RELOGIN_HINT = "run `fantabot login` to capture a fresh token"


class TokenError(Exception):
    """Base for everything in this module, so callers can catch the family."""


# --- the key ---------------------------------------------------------------


class KeyMissing(TokenError):
    """No `FANTABOT_ENCRYPTION_KEY`. Raised before a browser is ever opened."""

    def __init__(self) -> None:
        super().__init__(
            "FANTABOT_ENCRYPTION_KEY is not set. Generate one and put it in .env:\n"
            f"  {GENERATE_KEY_HINT}\n"
            "Nothing was opened and nothing was written."
        )


class KeyMalformed(TokenError):
    """Set, but not a Fernet key. The shape is named so it can be fixed."""

    def __init__(self) -> None:
        super().__init__(
            "FANTABOT_ENCRYPTION_KEY must be a 44-character urlsafe-base64 Fernet "
            f"key. Generate one with:\n  {GENERATE_KEY_HINT}"
        )


# --- the token itself ------------------------------------------------------


class TokenUnreadable(TokenError):
    """Not a decodable JWT. The input never reaches the message."""


class TokenUndecryptable(TokenError):
    """The stored ciphertext did not come back.

    Two causes, and telling them apart is the whole point of `key_fingerprint`:
    the key changed, or the row is corrupt. `cryptography` reports both as
    `InvalidToken`, five words that describe the symptom and not the cause.
    """


class TokenMissing(TokenError):
    """No row for this lega. Raised before a socket opens, not on a 401."""

    def __init__(self, league_id: int) -> None:
        super().__init__(
            f"no stored token for lega {league_id} — {RELOGIN_HINT}. "
            "Check what is stored with `fantabot token-status`."
        )


class TokenExpired(TokenError):
    """The `exp` claim has passed. A local check, not a round-trip."""

    def __init__(self, league_id: int, expired_on: str) -> None:
        super().__init__(
            f"the token for lega {league_id} expired on {expired_on} — {RELOGIN_HINT}."
        )


# --- the capture -----------------------------------------------------------


class LeagueMismatch(TokenError):
    """A `leagues[]` entry whose token decodes to a different `l_id`.

    The only check standing between a mislabelled row and acting in the wrong
    lega, so it refuses the whole capture rather than skipping the entry: if the
    blob's structure is not what it claims, its other entries are not evidence.
    """

    def __init__(self, entry_id: int, claim_id: int) -> None:
        super().__init__(
            f"the token under lega {entry_id} decodes to l_id {claim_id}. Nothing "
            "was stored — the localStorage blob is not the shape it claims, and "
            "storing the rest of it would risk acting in the wrong lega."
        )


class NoLeaguesFound(TokenError):
    """`LEAGUES2024_LOCAL` was absent, or held no leghe."""

    def __init__(self) -> None:
        super().__init__(
            "no leghe found in the browser session. If the page was still "
            "loading, finish signing in and try again; the Angular app writes "
            "LEAGUES2024_LOCAL only once the league list has rendered."
        )


# --- the transport ---------------------------------------------------------


class TokenRejected(TokenError):
    """`401 ATH001` — the token is invalid, expired early, or wrong-scoped."""

    def __init__(self, league_id: int) -> None:
        super().__init__(
            f"apileague rejected the token for lega {league_id} (ATH001). It may "
            f"have been revoked before its expiry — {RELOGIN_HINT}."
        )


class AppKeyRejected(TokenError):
    """`401 ATH007` — the static app_key is missing or has rotated."""

    def __init__(self) -> None:
        super().__init__(
            "apileague rejected the app_key (ATH007). It is a public constant "
            "shipped in the frontend bundle, so this means it rotated: re-grep "
            "the bundle following the recipe in docs/leghe-api.md."
        )


class ApiTimeout(TokenError):
    """The request did not complete. Says nothing about the token's validity."""

    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"apileague did not answer within {seconds:g}s. The stored token is "
            "untouched and may well be fine — retry, or skip verification with "
            "`--no-verify`."
        )


class ApiUnavailable(TokenError):
    """A non-401 failure. Also says nothing about the token."""

    def __init__(self, status: int) -> None:
        super().__init__(
            f"apileague returned {status}. The stored token is untouched; this is "
            "a problem at their end, not with your credentials."
        )
