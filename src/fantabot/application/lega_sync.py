"""`lega sync`: read everything the platform will tell us about the lega, and store it.

**Why one use case and not six commands.** These reads answer one question between them
-- what does the lega look like right now -- and they are only comparable when taken
together: a rosa priced at 474 credits means nothing without the budget that says 500,
and a fixture's `calculated` flag means nothing without the matchday that says which
round we are in. Six commands would let five of them succeed on Tuesday and the sixth on
Friday, and the table would then describe a lega that never existed.

**The failure model is per-read, not all-or-nothing.** One endpoint returning 400 must
not cost the other seven: the pool is several megabytes and the calendar is the only
place results ever appear, so a sync that abandons everything because `custom-roles`
hiccuped is a sync that silently stops running. Each read is attempted, failures are
collected and reported by name, and whatever came back is written. `ok` on the result is
False when anything failed, so a cron wrapper can still tell a partial sync from a clean
one.

**Writes happen once, at the end, in one transaction.** The reads are slow (the pool
alone is seconds) and holding a write transaction open across them would keep a
Postgres connection idle-in-transaction for the duration of a network call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fantabot.adapters.http import apileague
from fantabot.adapters.persistence.repositories.league import LeagueRepository
from fantabot.adapters.tokens.store import TokenStore
from fantabot.application.reporting import Reporter
from fantabot.domain.lega.models import (
    Competition,
    CustomRole,
    Fixture,
    LeagueState,
    PoolEntry,
    TeamRoster,
)
from fantabot.domain.lega.parse import (
    parse_competitions,
    parse_custom_roles,
    parse_fixtures,
    parse_league_state,
    parse_pool,
    parse_team_rosters,
)
from fantabot.domain.tokens.errors import TokenError


@dataclass
class SyncResult:
    """What one sync read, and what it could not."""

    league_id: int
    state: LeagueState | None = None
    rosters: tuple[TeamRoster, ...] = ()
    competitions: tuple[Competition, ...] = ()
    fixtures: tuple[Fixture, ...] = ()
    custom_roles: tuple[CustomRole, ...] = ()
    pool: tuple[PoolEntry, ...] = ()
    failures: list[str] = field(default_factory=list)
    written: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


def _attempt(result: SyncResult, what: str, read: Callable[[], Any]) -> Any | None:
    """Run one read, recording a failure by name instead of raising.

    `TokenError` is re-raised rather than collected: an expired or rejected token fails
    every read for the same reason, and reporting it eight times as eight unrelated
    outages hides the one thing the operator has to fix (`auth login`).
    """
    try:
        return read()
    except TokenError:
        raise
    except Exception as exc:
        result.failures.append(f"{what}: {type(exc).__name__}")
        return None


def collect(league_id: int, *, store: TokenStore, reporter: Reporter) -> SyncResult:
    """Every read, in one pass. No database, no writes — this half is pure network."""
    result = SyncResult(league_id=league_id)

    status = _attempt(result, "status", lambda: apileague.league_status(league_id, store=store))
    rosters_cfg = _attempt(
        result, "settings/rosters", lambda: apileague.roster_settings(league_id, store=store)
    )
    lineup_cfg = _attempt(
        result, "settings/lineup", lambda: apileague.lineup_settings(league_id, store=store)
    )
    if status is not None:
        result.state = parse_league_state(
            league_id, status, rosters_cfg or {}, lineup_cfg or {}
        )
        reporter.print(
            f"matchday [bold]{result.state.matchday}[/bold] · "
            f"budget {result.state.budget} · rosa {result.state.roster_size} · "
            f"{len(result.state.modules)} moduli"
        )

    teams_body = _attempt(result, "teams", lambda: apileague.teams(league_id, store=store))
    if teams_body is not None:
        result.rosters = _attempt(
            result, "teams/parse", lambda: parse_team_rosters(league_id, {"data": teams_body})
        ) or ()
        owned = sum(len(t.roster) for t in result.rosters)
        reporter.print(f"{len(result.rosters)} squadre · {owned} giocatori tesserati")

    comps_body = _attempt(
        result, "competitions", lambda: apileague.competitions(league_id, store=store)
    )
    if comps_body is not None:
        result.competitions = parse_competitions(league_id, comps_body)
        reporter.print(f"{len(result.competitions)} competizioni")

    fixtures: list[Fixture] = []
    for comp in result.competitions:
        if comp.deleted:
            continue

        def read_calendar(cid: int = comp.competition_id) -> list[dict[str, Any]]:
            return apileague.calendar(league_id, cid, store=store)

        body = _attempt(result, f"calendar/{comp.competition_id}", read_calendar)
        if body is not None:
            fixtures.extend(parse_fixtures(comp.competition_id, body))
    result.fixtures = tuple(fixtures)
    if fixtures:
        played = sum(1 for f in fixtures if f.calculated)
        reporter.print(f"{len(fixtures)} partite in calendario · {played} calcolate")

    roles_body = _attempt(
        result, "custom-roles", lambda: apileague.custom_roles(league_id, store=store)
    )
    if roles_body is not None:
        result.custom_roles = parse_custom_roles(league_id, roles_body)
        reporter.print(f"{len(result.custom_roles)} ruoli custom")

    pool_body = _attempt(result, "players", lambda: apileague.players(league_id, store=store))
    if pool_body is not None:
        result.pool = parse_pool(league_id, pool_body)
        reporter.print(f"{len(result.pool)} giocatori nel listone della lega")

    return result


def persist(result: SyncResult, repository: LeagueRepository) -> dict[str, int]:
    """Write whatever `collect` managed to read. Nothing here talks to the network."""
    written: dict[str, int] = {}
    if result.state is not None:
        repository.record_league_state(result.state)
        written["league_snapshot"] = 1
    if result.rosters:
        written["league_team_snapshot"] = repository.record_team_rosters(result.rosters)
    if result.competitions:
        written["league_competition"] = repository.record_competitions(result.competitions)
    if result.fixtures:
        written["league_fixture"] = repository.upsert_fixtures(result.fixtures)
    if result.custom_roles:
        written["league_custom_role"] = repository.record_custom_roles(result.custom_roles)
    if result.pool:
        written["league_player_pool"] = repository.record_pool(result.pool)
    result.written = written
    return written


__all__ = ["SyncResult", "collect", "persist"]
