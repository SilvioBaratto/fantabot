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
    #: The roster band. `min_player` is what the MAX cap reserves against, and it is often
    #: absent — 100 of 159 Mantra rooms in today's live registry carry no value. `None` rather
    #: than a default, so the cap can fail closed on a band it knows instead of quietly
    #: reserving nothing (`docs/fantalab/01:142`: the cap has no server backstop).
    min_player: int | None = None
    max_player: int | None = None
    #: How the room states its band, per `docs/fantalab/01-auction-engine.md` §"Roster
    #: selection modes" — `"no-limit-per-role"`, `"min-max-goalie-others"`, or a Classic-only
    #: mode this Mantra-only package never reads. `None` when the room's response carries none
    #: of the candidate keys `_parse_roster_band` tries — see that function for why the wire
    #: key names here are a permissive guess, not a confirmed contract.
    number_of_players_selection: str | None = None
    min_goalkeepers: int | None = None
    max_goalkeepers: int | None = None
    min_others: int | None = None
    max_others: int | None = None
    #: "Start from the quotazione" instead of from 1. It deletes the whole 1-3 credit tail, so
    #: every price model downstream has to know. FantaLab's default is false.
    call_at_quotaz: bool = False
    #: The Classic per-role band under `number_of_players_selection == "static"` — a
    #: `{P,D,C,A}` map of exact counts (`min == max`). Confirmed live 3584692:
    #: `{"P":3,"D":8,"C":8,"A":6}` (`docs/classic/task0-capture.md`). `None` for a Mantra room,
    #: which declares its band through the goalkeeper/others keys instead.
    players_settings_data: Mapping[str, int] | None = None

    def free_seats(self) -> tuple[Seat, ...]:
        """The seats nobody holds — a bot claims one of these to bid legitimately."""
        return tuple(seat for seat in self.seats if seat.user_id is None)

    def seat_of(self, user_id: str) -> Seat | None:
        """Our seat, matched on the FantaLab uuid we already hold. `None` if we are not in.

        Looked up rather than typed because four id spaces are in play and the platform
        validates none of them: a raise naming a foreign seat is accepted with a 200
        (`docs/fantalab/06 §10.1`, test 7). A leghe.fantacalcio.it team id pasted here would
        not error — it would drive somebody else's team all evening.

        A falsy `user_id` never matches, or an absent uid would be handed the first free chair
        in the room, every one of which carries `user_id: None`.
        """
        if not user_id:
            return None
        return next((seat for seat in self.seats if seat.user_id == user_id), None)


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


#: Candidate wire keys for the goalkeeper/outfield band, tried in order. `docs/fantalab/
#: 01-auction-engine.md` and `04-simulator-spec.md` record `min_goalkeepers`/`max_goalkeepers`/
#: `min_others`/`max_others` — the internal `AstaConfig` dataclass's own field names, and the
#: primary bet, marked ⏳ in both docs because neither was ever checked against a live response
#: carrying `number_of_players_selection: "min-max-goalie-others"`. The Italian aliases are a
#: real possibility the platform's UI is entirely Italian ("P Min", "Mov Min") and some other
#: fields on this same endpoint could plausibly follow suit, though none observed so far do.
_GOALKEEPER_BAND_KEYS: dict[str, tuple[str, ...]] = {
    "min_goalkeepers": ("min_goalkeepers", "min_portieri", "minGoalkeepers"),
    "max_goalkeepers": ("max_goalkeepers", "max_portieri", "maxGoalkeepers"),
    "min_others": ("min_others", "min_altri", "minOthers"),
    "max_others": ("max_others", "max_altri", "maxOthers"),
}


def _parse_roster_band(body: Mapping[str, Any]) -> dict[str, int | None]:
    """The goalkeeper/outfield band, tried across plausible wire spellings. Pure.

    **Permissive on purpose, not a confirmed contract.** Betting on one exact key and getting
    it wrong would silently read every `min-max-goalie-others` room as if it declared nothing
    — indistinguishable from `rules_for_room`'s honest "assumed" fallback, which is the one
    outcome this has to avoid being mistaken for. Trying several candidates and taking the
    first that parses is strictly safer than guessing once: a wrong guess here still falls
    through to the same safe default a room that truly declares nothing gets, never a wrong
    number silently used as if it were real.
    """
    found: dict[str, int | None] = {}
    for field, candidates in _GOALKEEPER_BAND_KEYS.items():
        value = None
        for key in candidates:
            value = _as_int(body.get(key))
            if value is not None:
                break
        found[field] = value
    return found


def _players_settings(body: Mapping[str, Any]) -> Mapping[str, int] | None:
    """The Classic ``static`` per-role band, or ``None``.

    Reads only the P/D/C/A keys and coerces each to int; a body without a usable map (a Mantra
    room, or a malformed one) returns ``None`` so the caller falls back rather than inventing a
    band. See ``RoomConfig.players_settings_data``.
    """
    raw = body.get("players_settings_data")
    if not isinstance(raw, Mapping):
        return None
    band = {role: _as_int(raw.get(role)) for role in ("P", "D", "C", "A")}
    resolved = {role: value for role, value in band.items() if value is not None}
    return resolved or None


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
        min_player=_as_int(body.get("min_player")),
        max_player=_as_int(body.get("max_player")),
        call_at_quotaz=bool(body.get("call_at_quotaz")),
        number_of_players_selection=_as_str(body.get("number_of_players_selection")),
        players_settings_data=_players_settings(body),
        **_parse_roster_band(body),
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
