"""Own-room reads over `api.fantalab.it`.

`docs/fantalab/06-asta-write-path.md` §3: ``POST /fantaleague/fetch`` and ``GET /fantaleagues/live``
**require a Bearer** (measured `401` unauthenticated, 2026-08-28) — only the RTDB nodes and the
player CDN are public. This module wraps the reads the live advisory uses to *discover* a room —
``fetch_league`` (config, seats, RTDB shard) and ``live_leagues`` — plus ``join_team``. Each takes
an optional ``token``; without one the call is unauthenticated and will `401`.

A **participant bot needs none of these**: told its shard, seat and uid, it reads the live lot and
bids entirely over the unauthenticated RTDB (``rtdb``). These calls matter only when discovering a
room or acting as admin, which is where a ``token`` comes from.

The parse is pure (``parse_league``); the HTTP call is a thin shell with an **injectable
transport** so the suite never builds a real one — the socket-free default tier. A ``token``, when
given, rides in the ``Authorization`` header and is never logged; a tokened caller must mask its
own errors (an httpx traceback renders request headers) — deferred to the admin-auth task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

FETCH_PATH = "/fantaleague/fetch"
LIVE_PATH = "/fantaleagues/live"
JOIN_PATH = "/fantaleague/join"
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class Seat:
    """One team's seat in the room. ``user_id is None`` means the seat is free."""

    fantateam_id: str
    user_id: str | None
    position: int | None
    team_name: str | None
    max_credits: int | None


@dataclass(frozen=True)
class RoomConfig:
    """A room's configuration, flat off ``fantaleague/fetch``.

    ``db`` is the resolved RTDB shard index (``None`` = the default namespace), the routing key
    for every realtime subscription — see ``shard_of``.
    """

    fantaleague_id: str
    admin_id: str | None
    db: int | None
    asta_type: str | None
    asta_mode: str | None
    raise_mode: str | None
    num_teams: int | None
    num_credits: int | None
    counter_time: int | None
    counter_time_first: int | None
    season: str | None
    is_live: bool
    auction_running: bool
    seats: tuple[Seat, ...]

    def free_seats(self) -> tuple[Seat, ...]:
        """The seats nobody holds — a bot claims one of these to bid legitimately."""
        return tuple(seat for seat in self.seats if seat.user_id is None)


def shard_of(db: Any) -> int | None:
    """The RTDB shard index for a room's ``db`` field. ``None``/absent → the default namespace.

    FantaLab sends it as a string (``"9"``) or occasionally an int; both fold to int. A blank or
    unparseable value is the default namespace, **not** shard 0 — guessing a shard would route a
    subscription at the wrong database. ``bool`` is an int subclass, so it is refused explicitly.
    """
    if db is None or isinstance(db, bool):
        return None
    if isinstance(db, int):
        return db
    text = str(db).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_seat(raw: Mapping[str, Any]) -> Seat:
    return Seat(
        fantateam_id=str(raw.get("fantateam_id")),
        user_id=_as_str(raw.get("user_id")),
        position=_as_int(raw.get("position")),
        team_name=_as_str(raw.get("team_name")),
        max_credits=_as_int(raw.get("max_credits")),
    )


def parse_league(body: Mapping[str, Any]) -> RoomConfig:
    """The flat ``fantaleague/fetch`` record → a typed ``RoomConfig``. Pure."""
    seats = tuple(
        _parse_seat(team) for team in body.get("fantateams", []) if isinstance(team, Mapping)
    )
    return RoomConfig(
        fantaleague_id=str(body.get("fantaleague_id")),
        admin_id=_as_str(body.get("admin_id")),
        db=shard_of(body.get("db")),
        asta_type=_as_str(body.get("asta_type")),
        asta_mode=_as_str(body.get("asta_mode")),
        raise_mode=_as_str(body.get("raise_mode")),
        num_teams=_as_int(body.get("num_teams")),
        num_credits=_as_int(body.get("num_credits")),
        counter_time=_as_int(body.get("counter_time")),
        counter_time_first=_as_int(body.get("counter_time_first")),
        season=_as_str(body.get("season")),
        is_live=bool(body.get("is_live")),
        auction_running=bool(body.get("auction_running")),
        seats=seats,
    )


def _base_url() -> str:
    from fantabot.config import settings

    return settings.fantabot_fantalab_base_url


def _headers(token: str | None) -> dict[str, str]:
    """The ``Authorization`` header when a token is given, else nothing. Never logged."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_league(
    fantaleague_id: str,
    *,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> RoomConfig:
    """``POST /fantaleague/fetch`` → the room config. **Needs a Bearer** (`401` without one).

    ``transport`` is injectable so tests never construct a real one — what keeps this in the
    socket-free tier. A participant bot does not call this; it is given the shard and seat.
    """
    with httpx.Client(
        base_url=_base_url(), headers=_headers(token), timeout=timeout, transport=transport
    ) as client:
        response = client.post(
            FETCH_PATH, json={"fantaleague_id": fantaleague_id, "type": "fantaleague"}
        )
    response.raise_for_status()
    body = response.json()
    return parse_league(body if isinstance(body, dict) else {})


def live_leagues(
    *,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[RoomConfig]:
    """``GET /fantaleagues/live`` → the list of running auctions. **Needs a Bearer** (`401` without)."""
    with httpx.Client(
        base_url=_base_url(), headers=_headers(token), timeout=timeout, transport=transport
    ) as client:
        response = client.get(LIVE_PATH)
    response.raise_for_status()
    body = response.json()
    if isinstance(body, list):
        rows: Any = body
    elif isinstance(body, dict):
        rows = body.get("data", [])
    else:
        rows = []
    return [parse_league(row) for row in rows if isinstance(row, Mapping)]


def join_team(
    fantateam_id: str,
    user_id: str,
    *,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """``POST /fantaleague/join`` — claim a seat. Returns ``True`` on success. **Needs a Bearer.**

    The body is exactly ``{fantateam_id, user_id}``: ``invitation_id`` is **not** required when
    the seat's id is already known (``docs/fantalab/06-asta-write-path.md`` §3, observed). A seat
    is claimed once (interactively, with a token); the bot then bids on it over the unauthenticated
    RTDB, so a headless participant never calls this itself.
    """
    with httpx.Client(
        base_url=_base_url(), headers=_headers(token), timeout=timeout, transport=transport
    ) as client:
        response = client.post(JOIN_PATH, json={"fantateam_id": fantateam_id, "user_id": user_id})
    response.raise_for_status()
    return True


__all__ = [
    "RoomConfig",
    "Seat",
    "fetch_league",
    "join_team",
    "live_leagues",
    "parse_league",
    "shard_of",
]
