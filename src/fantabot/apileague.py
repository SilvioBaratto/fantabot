"""The `apileague.fantacalcio.it` client. One endpoint, and the headers it needs.

Reference: `docs/leghe-api.md`. Every request needs two headers — the static
`app_key`, and a `Bearer` token scoped to one lega.

**Only `GET /onboarding/v1/league/status` is wrapped.** SPEC's Non-goals fence
off `competitions`, `teams`, `teams/my`, `settings/rosters` and `market/v1/time`;
they are documented and stay documented until something needs them.

**No `httpx` exception is ever re-raised.** `httpx.RequestError` carries its
`.request`, and a rendered traceback can surface the `Authorization` header.
Messages here are built from the status code and the body's `code`/`message`
fields only — never the body verbatim, never the exception.

For the same reason: **do not enable `httpx` or `httpcore` trace-level logging.**
Both print request headers, which is the credential.
"""

from __future__ import annotations

from typing import Any

import httpx

from fantabot.tokens.errors import (
    ApiTimeout,
    ApiUnavailable,
    AppKeyRejected,
    TokenExpired,
    TokenMissing,
    TokenRejected,
)
from fantabot.tokens.store import TokenStore

# Static and public: shipped in the frontend's JS bundle, identical for every
# user, and `docs/leghe-api.md` is explicit that it is not a secret. A module
# constant rather than a Settings field — SPEC's Tech Stack names exactly two new
# settings, and putting a documented-public value behind the config-check mask
# would teach the wrong thing about what that mask means.
#
# If it ever rotates (an ATH007 despite sending it), re-grep the shipped bundle:
# fetch https://leghe.fantacalcio.it/resources/main-*.js, follow its re-export to
# the content-hashed main-*.js, and search for `appKey` near `production:!0`.
# `docs/leghe-api.md`, "How this was found", step 3.
APP_KEY = "ICiELOObd5DF5uJEATi77CRvHiiRuMU0"

LEAGUE_STATUS_PATH = "/onboarding/v1/league/status"
DEFAULT_TIMEOUT = 10.0


def auth_headers(league_id: int, *, store: TokenStore, now: Any = None) -> dict[str, str]:
    """The two headers, or a sentence saying why not — **before any socket opens**.

    SC 13. `store.load_plaintext` does the missing/expired/undecryptable checks
    against plaintext columns, so a dead token is refused without decrypting
    anything and without a round-trip to be told what we already knew.
    """
    token = store.load_plaintext(league_id, now=now)
    return {"app_key": APP_KEY, "Authorization": f"Bearer {token}"}


def _base_url() -> str:
    from fantabot.config import settings

    return settings.fantabot_apileague_base_url


def _error_code(response: httpx.Response) -> str:
    """The body's `code` field, or `""`. Never the body itself."""
    try:
        body = response.json()
    except (ValueError, TypeError):
        return ""
    return str(body.get("code", "")) if isinstance(body, dict) else ""


def _raise_for(response: httpx.Response, league_id: int) -> None:
    """Map a failure onto a sentence with an action attached.

    Keyed on the body's `code`, per `docs/leghe-api.md`'s error table — the two
    401s mean entirely different things and have different fixes.
    """
    if response.status_code == 401:
        code = _error_code(response)
        if code == "ATH007":
            raise AppKeyRejected()
        # ATH001, and any unlabelled 401: the credential is the thing being
        # rejected, and re-login is the answer either way.
        raise TokenRejected(league_id)
    if response.status_code >= 400:
        raise ApiUnavailable(response.status_code)


def league_status(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /onboarding/v1/league/status` — the one call that proves a token works.

    `transport` is injectable so tests never build a default transport or an SSL
    context, which is what keeps this suite in the socket-free default tier.
    """
    headers = auth_headers(league_id, store=store, now=now)

    try:
        with httpx.Client(
            base_url=_base_url(), headers=headers, timeout=timeout, transport=transport
        ) as client:
            response = client.get(LEAGUE_STATUS_PATH)
    except httpx.TimeoutException:
        # Deliberately not `from exc`: httpx.RequestError carries .request, and
        # a chained traceback can render the Authorization header.
        raise ApiTimeout(timeout) from None
    except httpx.TransportError:
        raise ApiUnavailable(0) from None

    _raise_for(response, league_id)

    body = response.json()
    return body if isinstance(body, dict) else {}


__all__ = [
    "APP_KEY",
    "ApiTimeout",
    "ApiUnavailable",
    "AppKeyRejected",
    "TokenExpired",
    "TokenMissing",
    "TokenRejected",
    "auth_headers",
    "league_status",
]
