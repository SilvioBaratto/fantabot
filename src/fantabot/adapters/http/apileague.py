"""The `apileague.fantacalcio.it` client. Two endpoints, and the headers they need.

Reference: `docs/leghe-api.md`. Every request needs two headers — the static
`app_key`, and a `Bearer` token scoped to one lega.

**The read surface is now the whole lega**, not just our own team: `lega sync` needs
every team's rosa, the calendar, the pool and the custom roles, so `teams`, `players`,
`roster_settings`, `custom_roles` and `calendar` are wrapped here alongside the two
originals. `market/v1/time` stays documented and unwrapped — nothing needs the server
clock.

**`GET /onboarding/v1/league/profile` is deliberately NOT wrapped.** It returns the
lega's join password in `parola`, and a wrapper is an invitation to store or print it.
The lega name and president it also carries are available from `teams` without touching
a shared secret.

**No `httpx` exception is ever re-raised.** `httpx.RequestError` carries its
`.request`, and a rendered traceback can surface the `Authorization` header.
Messages here are built from the status code and the body's `code`/`message`
fields only — never the body verbatim, never the exception.

For the same reason: **do not enable `httpx` or `httpcore` trace-level logging.**
Both print request headers, which is the credential.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from fantabot.adapters.tokens.store import TokenStore
from fantabot.domain.lineup.errors import LineupRejected
from fantabot.domain.tokens.errors import (
    ApiTimeout,
    ApiUnavailable,
    AppKeyRejected,
    TokenExpired,
    TokenMissing,
    TokenRejected,
)

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
TEAMS_MY_PATH = "/onboarding/v1/league/teams/my"
COMPETITIONS_PATH = "/onboarding/v1/league/competitions"
LINEUP_SETTINGS_PATH = "/onboarding/v1/league/settings/lineup"
ROSTER_SETTINGS_PATH = "/onboarding/v1/league/settings/rosters"
TEAMS_PATH = "/onboarding/v1/league/teams"
PLAYERS_PATH = "/onboarding/v1/league/players"
CUSTOM_ROLES_PATH = "/onboarding/v1/league/custom-roles"
CALENDAR_PATH = "/onboarding/v1/league/competition/calendar"
DEFAULT_TIMEOUT = 10.0

#: `league/teams` pages. The frontend asks for 50 and so do we; a lega of 8 fits in one
#: page, and the loop below still follows `pages` because a lega of 60 does not.
TEAMS_PAGE_SIZE = 50

# The lineup lives under a different microservice, `gaming/v1`, not `onboarding/v1`
# (`docs/leghe-api.md`). `division` is the divisione tag, `A` for this account's leghe.
DEFAULT_DIVISION = "A"


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


def _send(
    method: str,
    path: str,
    league_id: int,
    *,
    body: Mapping[str, Any] | None,
    store: TokenStore,
    transport: httpx.BaseTransport | None,
    timeout: float,
    now: Any,
    raise_for: Callable[[httpx.Response, int], None],
) -> Any:
    """The one authenticated request, its leak guard, and the JSON parse — shared by every
    read and write below. `transport` is injectable so tests never build a default transport
    or an SSL context, which keeps this suite in the socket-free tier.

    **No `httpx` exception, and no raw JSON parse error, is ever re-raised** — every failure
    exits through `from None`. Both `httpx.RequestError.request` and a bare traceback can
    render the `Authorization` header, so the guard catches the whole `httpx.HTTPError`
    family (not only timeout/transport — a `DecodingError` behind an intercepting proxy is
    one) and the `ValueError` a non-JSON body raises, mapping each to a token-free error.
    `raise_for` is the status mapper: `_raise_for` for reads, `_raise_for_lineup` for submit.
    """
    headers = auth_headers(league_id, store=store, now=now)
    content = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(body)

    try:
        with httpx.Client(
            base_url=_base_url(), headers=headers, timeout=timeout, transport=transport
        ) as client:
            response = client.request(method, path, content=content)
    except httpx.TimeoutException:
        raise ApiTimeout(timeout) from None
    except httpx.HTTPError:
        raise ApiUnavailable(0) from None

    raise_for(response, league_id)

    try:
        return response.json()
    except ValueError:
        raise ApiUnavailable(response.status_code) from None


def _get(
    path: str,
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None,
    timeout: float,
    now: Any,
) -> dict[str, Any]:
    """One authenticated `GET`, coerced to a dict (the object endpoints)."""
    body = _get_raw(path, league_id, store=store, transport=transport, timeout=timeout, now=now)
    return body if isinstance(body, dict) else {}


def _get_raw(
    path: str,
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None,
    timeout: float,
    now: Any,
) -> Any:
    """An authenticated `GET` returning the parsed JSON as-is (list or dict)."""
    return _send(
        "GET", path, league_id, body=None, store=store, transport=transport,
        timeout=timeout, now=now, raise_for=_raise_for,
    )


def _raise_for_lineup(response: httpx.Response, league_id: int) -> None:
    """`_raise_for`, plus the write path's `LUP0xx`.

    The lineup submit answers an unfieldable formation with a `400` and a `LUP` code
    (`LUP009` observed) — a lineup problem, not a token or server fault, so it maps to a
    `LineupRejected` before the generic 4xx handling ever runs.
    """
    if response.status_code == 400:
        code = _error_code(response)
        if code.startswith("LUP"):
            raise LineupRejected(code)
    _raise_for(response, league_id)


def _post(
    path: str,
    league_id: int,
    *,
    body: Mapping[str, Any],
    store: TokenStore,
    transport: httpx.BaseTransport | None,
    timeout: float,
    now: Any,
) -> dict[str, Any]:
    """One authenticated `POST` with a JSON body — the write sibling of `_get`.

    Shares `_send`'s leak guard; the one difference is the error mapping —
    `_raise_for_lineup` adds the `LUP0xx` rejection the write path can return.
    """
    parsed = _send(
        "POST", path, league_id, body=body, store=store, transport=transport,
        timeout=timeout, now=now, raise_for=_raise_for_lineup,
    )
    return parsed if isinstance(parsed, dict) else {}


def league_status(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /onboarding/v1/league/status` — the one call that proves a token works."""
    return _get(
        LEAGUE_STATUS_PATH, league_id, store=store, transport=transport, timeout=timeout, now=now
    )


def my_team(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /onboarding/v1/league/teams/my` — the caller's own team: credits and roster.

    Same shape as one item of `GET /onboarding/v1/league/teams` (`docs/leghe-api.md`),
    which stays unwrapped — every team in the lega is a different, larger claim than
    this one, and nothing needs it yet.
    """
    return _get(
        TEAMS_MY_PATH, league_id, store=store, transport=transport, timeout=timeout, now=now
    )


def competitions(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> list[dict[str, Any]]:
    """`GET /onboarding/v1/league/competitions` — the league's competitions (a JSON array).

    Each entry carries `id`, `tmids`, `sDay`/`eDay`, `del` (`docs/leghe-api.md`). Used to
    resolve which competition a lineup acts on.
    """
    body = _get_raw(
        COMPETITIONS_PATH, league_id, store=store, transport=transport, timeout=timeout, now=now
    )
    return [c for c in body if isinstance(c, dict)] if isinstance(body, list) else []


def lineup_settings(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /onboarding/v1/league/settings/lineup` — the formation settings: allowed modules
    (`mods`), bench size (`tbench`), deadline, captain (`docs/leghe-api.md`)."""
    return _get(
        LINEUP_SETTINGS_PATH, league_id, store=store, transport=transport, timeout=timeout, now=now
    )


def roster_settings(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /onboarding/v1/league/settings/rosters` — budget, roster size, and the
    per-role-group min/max (`docs/leghe-api.md`). `sroles` says how many groups the
    `minrl`/`maxrl` arrays have, and it differs between this account's two leghe."""
    return _get(
        ROSTER_SETTINGS_PATH, league_id, store=store, transport=transport,
        timeout=timeout, now=now,
    )


def teams(
    league_id: int,
    *,
    division: str = DEFAULT_DIVISION,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> list[dict[str, Any]]:
    """`GET /onboarding/v1/league/teams` — **every** team in the lega, rosa included.

    Each item carries `cal` and `cs`: the team's 30 player ids and the 30 costs paid for
    them, two `;`-joined parallel strings. This is the only read that shows what the
    opponents bought, and it needs no admin rights.

    Pages are followed to exhaustion. `pages` is authoritative; the loop also stops on an
    empty page so a server that reports the wrong count cannot spin it.
    """
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        query = f"?page={page}&pageSize={TEAMS_PAGE_SIZE}&division={division.upper()}"
        body = _get(
            f"{TEAMS_PATH}{query}", league_id, store=store, transport=transport,
            timeout=timeout, now=now,
        )
        rows = body.get("data")
        batch = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        out.extend(batch)
        pages = body.get("pages")
        if not batch or not isinstance(pages, int) or page >= pages:
            return out
        page += 1


def players(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 30.0,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /onboarding/v1/league/players` — the lega's own player list.

    An object with a `players` key, **not** a bare array (`docs/leghe-api.md`); the
    default timeout is raised because the body is several megabytes.
    """
    return _get(
        PLAYERS_PATH, league_id, store=store, transport=transport, timeout=timeout, now=now
    )


def custom_roles(
    league_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> list[dict[str, Any]]:
    """`GET /onboarding/v1/league/custom-roles` — players this lega re-tagged (a JSON
    array). Small, and load-bearing: L1 must match on the lega's role, not the listone's.
    """
    body = _get_raw(
        CUSTOM_ROLES_PATH, league_id, store=store, transport=transport, timeout=timeout, now=now
    )
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


def calendar(
    league_id: int,
    competition_id: int,
    *,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> list[dict[str, Any]]:
    """`GET /onboarding/v1/league/competition/calendar/{id}` — the whole calendar.

    One entry per round, each holding its `matches` with points, results and the two
    matchday numberings. Found by grepping the shipped Angular bundle for the service
    that backs the Fixtures page (`docs/leghe-api.md`, "How this was found").
    """
    body = _get_raw(
        f"{CALENDAR_PATH}/{competition_id}", league_id, store=store, transport=transport,
        timeout=timeout, now=now,
    )
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


def teamLineup_read(
    league_id: int,
    competition_id: int,
    *,
    division: str = DEFAULT_DIVISION,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`GET /gaming/v1/teamLineup/visualizza/{division}/{competition_id}` — the current
    lineup, `{teamLineupDto, lineUpInfo}` (`docs/leghe-api.md`).

    A different microservice (`gaming/v1`) from the `onboarding/v1` reads above, but the
    same two headers and the same `_get` leak guard.
    """
    path = f"/gaming/v1/teamLineup/visualizza/{division}/{competition_id}"
    return _get(path, league_id, store=store, transport=transport, timeout=timeout, now=now)


def teamLineup_submit(
    league_id: int,
    payload: Mapping[str, Any],
    *,
    division: str = DEFAULT_DIVISION,
    store: TokenStore,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    now: Any = None,
) -> dict[str, Any]:
    """`POST /gaming/v1/teamLineup/{division}` — submit the formation.

    `200` and the saved DTO on success; an unfieldable formation returns a `LUP0xx` `400`
    that becomes `LineupRejected` (`docs/leghe-api.md`). `payload` is the decoded body
    built by `domain/lineup/payload`, already validated against the schema by the caller.
    """
    path = f"/gaming/v1/teamLineup/{division}"
    return _post(
        path, league_id, body=payload, store=store, transport=transport, timeout=timeout, now=now
    )


__all__ = [
    "APP_KEY",
    "ApiTimeout",
    "ApiUnavailable",
    "AppKeyRejected",
    "TokenExpired",
    "TokenMissing",
    "TokenRejected",
    "auth_headers",
    "calendar",
    "competitions",
    "custom_roles",
    "league_status",
    "lineup_settings",
    "my_team",
    "players",
    "roster_settings",
    "teamLineup_read",
    "teamLineup_submit",
    "teams",
]
