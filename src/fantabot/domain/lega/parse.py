"""Translate `apileague.fantacalcio.it`'s bodies into `models`. Pure: no I/O, no clock.

**`MARLE_TO_CODE` is measured, not guessed.** `docs/leghe-api.md` listed the numeric
role codes as an open gap — "twelve and twelve is suggestive, not proof". The map below
was resolved by joining `GET /onboarding/v1/league/players` against the `quotazioni`
Mantra listone on `player_id` and pairing the two role lists positionally: 571 of 588
players matched, and every integer resolved to exactly one letter code with no
runners-up. `17` and `18` never appeared and are absent rather than invented.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from fantabot.domain.lega.models import (
    Competition,
    CustomRole,
    Fixture,
    LeagueState,
    PoolEntry,
    RosterSlot,
    TeamRoster,
)

#: `marle` integer -> canonical Mantra code. See the module docstring.
MARLE_TO_CODE: Mapping[int, str] = {
    6: "POR", 7: "DD", 8: "DS", 9: "DC", 10: "E", 11: "M",
    12: "C", 13: "T", 14: "W", 15: "A", 16: "PC", 19: "B",
}

#: `custom-roles`' integers -> **Classic** macro role. A different scale from `marle`,
#: and measured the same way: all 27 overrides on 2026-09-02 joined against the Classic
#: listone, `2 -> D` (3 rows), `3 -> C` (20), `4 -> A` (4), no disagreement. `1` was not
#: observed and is included only because a four-value P/D/C/A scale with three of its
#: values pinned leaves it nothing else to be.
#:
#: This endpoint is Classic-only. It says nothing about Mantra roles and must not reach
#: L1: the overrides are moves like Zaccagni C -> A, which the Mantra listone already
#: expresses as `W`/`A` on its own.
CLASSIC_ROLE_CODES: Mapping[int, str] = {1: "P", 2: "D", 3: "C", 4: "A"}


class LeaguePayloadError(ValueError):
    """A body that cannot be believed — not a field we do not understand, but a body
    whose own parts contradict each other."""


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float, str)) and str(value).strip() else None


def _ids(raw: Any) -> tuple[int, ...]:
    """A `;`-joined id list. Empty string and `None` are both "no players", not an error."""
    if not raw:
        return ()
    return tuple(int(part) for part in str(raw).split(";") if part.strip())


def classic_role_code(value: Any) -> str:
    """One `custom-roles` integer as its Classic macro role, verbatim when unmapped."""
    number = _int(value)
    if number is None:
        return str(value)
    return CLASSIC_ROLE_CODES.get(number, str(number))


def role_code(value: Any) -> str:
    """One `marle` integer as its Mantra code, or `str(value)` when it is unmapped.

    An unknown integer is kept verbatim rather than dropped: a role we cannot name is a
    fact about the lega, and losing it would make a 30-man rosa silently 29.
    """
    number = _int(value)
    if number is None:
        return str(value)
    return MARLE_TO_CODE.get(number, str(number))


def parse_matchday_start(raw: Any) -> datetime | None:
    """`mstr`, an ISO datetime with no zone (`2026-08-22T16:30:00`). Naive is kept naive:
    the platform sends Europe/Rome wall time and inventing UTC here would move kickoff."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def parse_league_state(
    league_id: int,
    status: Mapping[str, Any],
    rosters: Mapping[str, Any],
    lineup: Mapping[str, Any],
) -> LeagueState:
    """`league/status` + `settings/rosters` + `settings/lineup` as one record."""
    return LeagueState(
        league_id=league_id,
        season_id=_int(status.get("sId")),
        matchday=_int(status.get("mday")),
        matchday_start=parse_matchday_start(status.get("mstr")),
        active=bool(status.get("activ")),
        stopped=bool(status.get("sto")),
        budget=_int(rosters.get("budg")),
        roster_size=_int(rosters.get("xsltc")),
        role_groups=_int(rosters.get("sroles")),
        min_roles=tuple(int(n) for n in rosters.get("minrl") or ()),
        max_roles=tuple(int(n) for n in rosters.get("maxrl") or ()),
        modules=tuple(str(m) for m in lineup.get("mods") or ()),
        bench_size=_int(lineup.get("tbench")),
        captain_slots=_int(lineup.get("lcap")),
    )


def parse_team_roster(league_id: int, body: Mapping[str, Any]) -> TeamRoster:
    """One item of `league/teams`, rosa included.

    Raises `LeaguePayloadError` when `cal` and `cs` disagree in length. They are two
    parallel lists in one JSON object with nothing tying a cost to an id but its
    position, so a mismatch is unrecoverable — and pairing them anyway would attribute
    one player's price to another.
    """
    ids = _ids(body.get("cal"))
    costs = _ids(body.get("cs"))
    if len(ids) != len(costs):
        raise LeaguePayloadError(
            f"team {body.get('id')}: {len(ids)} roster ids but {len(costs)} costs"
        )
    return TeamRoster(
        league_id=league_id,
        team_id=int(body["id"]),
        user_id=_int(body.get("idu")),
        nome=str(body.get("n", "")),
        owner=str(body.get("nu", "")),
        division=str(body.get("d") or "A"),
        credits_initial=_int(body.get("cri")),
        credits_spent=_int(body.get("crs")),
        credits_remaining=_int(body.get("cr")),
        roster=tuple(RosterSlot(player_id=p, cost=c) for p, c in zip(ids, costs, strict=True)),
    )


def parse_team_rosters(league_id: int, body: Mapping[str, Any]) -> tuple[TeamRoster, ...]:
    """A whole page of `league/teams`."""
    data = body.get("data")
    rows = data if isinstance(data, list) else []
    return tuple(parse_team_roster(league_id, row) for row in rows if isinstance(row, dict))


def parse_competitions(
    league_id: int, body: Sequence[Mapping[str, Any]]
) -> tuple[Competition, ...]:
    """`league/competitions`, a bare array."""
    return tuple(
        Competition(
            league_id=league_id,
            competition_id=int(row["id"]),
            name=str(row.get("name", "")),
            tipo=_int(row.get("type")),
            start_day=_int(row.get("sDay")),
            end_day=_int(row.get("eDay")),
            team_ids=tuple(int(t) for t in row.get("tmids") or ()),
            deleted=bool(row.get("del")),
        )
        for row in body
        if isinstance(row, dict) and row.get("id") is not None
    )


def parse_fixtures(
    competition_id: int, body: Sequence[Mapping[str, Any]]
) -> tuple[Fixture, ...]:
    """`league/competition/calendar/{id}` — rounds, each holding its matches."""
    out: list[Fixture] = []
    for round_ in body:
        if not isinstance(round_, dict):
            continue
        matchday = _int(round_.get("matchDay"))
        if matchday is None:
            continue
        for match in round_.get("matches") or ():
            if not isinstance(match, dict):
                continue
            out.append(
                Fixture(
                    competition_id=competition_id,
                    matchday=matchday,
                    championship_matchday=_int(round_.get("championshipMatchDay")),
                    team_home=int(match["tIdH"]),
                    team_away=int(match["tIdA"]),
                    points_home=_float(match.get("ptH")),
                    points_away=_float(match.get("ptA")),
                    standing_home=_int(match.get("standingPtH")),
                    standing_away=_int(match.get("standingPtA")),
                    result=_text(match.get("result")),
                    real_result=_text(match.get("resultSR")),
                    calculated=bool(round_.get("calculated")),
                )
            )
    return tuple(out)


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _text(value: Any) -> str | None:
    """A result string. `"-"` and `""` both mean "not played" and become `None`."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped and stripped != "-" else None


def parse_custom_roles(
    league_id: int, body: Sequence[Mapping[str, Any]]
) -> tuple[CustomRole, ...]:
    """`league/custom-roles` — the players this lega re-tagged, in **Classic** roles."""
    return tuple(
        CustomRole(
            league_id=league_id,
            player_id=int(row["id"]),
            nome=str(row.get("name", "")),
            club=str(row.get("team", "")),
            original_role=classic_role_code(row.get("originalRole")),
            role=classic_role_code(row.get("role")),
        )
        for row in body
        if isinstance(row, dict) and row.get("id") is not None
    )


def parse_pool(league_id: int, body: Mapping[str, Any]) -> tuple[PoolEntry, ...]:
    """`league/players`. An object with a `players` key, never a bare array — code
    written against a top-level list gets `KeyError: 0` (`docs/leghe-api.md`)."""
    players = body.get("players")
    rows = players if isinstance(players, list) else []
    return tuple(
        PoolEntry(
            league_id=league_id,
            player_id=int(row["id"]),
            nome=str(row.get("name", "")),
            club=str(row.get("stnme", "")),
            quotazione=_int(row.get("quotd")),
            fvm_classic=_int(row.get("fvmfc")),
            fvm_mantra=_int(row.get("fvmma")),
            ruoli_codice=tuple(role_code(r) for r in row.get("marle") or ()),
        )
        for row in rows
        if isinstance(row, dict) and row.get("id") is not None
    )
