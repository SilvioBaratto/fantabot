"""The two one-shot team commands: `db snapshot-team` and `db backfill-teams`.

They are here for the same reason `exclusions.py` is: both Typer bodies held decisions
the app needs, and a decision the app cannot call is a decision the app reimplements.

**`snapshot-team` is three choices, not one call.** Which endpoint says what our own
credits are (`teams/my`, not `teams`, though the two share a row shape), which function
translates its abbreviated keys, and which repository method writes it. A second surface
picking any of the three differently stores a row that looks right and is not — and
`league_team_snapshot` is append-only, so it stays.

**`league_id` is a parameter and is never read back out of the body.** `teams/my` carries
the team id and its owner and no league id at all (`domain/shared/league.py`). The caller
is the only thing that knows which lega it asked, so asking one lega and recording under
another is a mistake nothing downstream can detect.

**Nothing here commits**, on `exclusions.py`'s reasoning: `database_manager.get_session`
commits on clean exit, and a function that committed on its caller's behalf would take
the transaction boundary away from the one place that knows what else is in it.

**`backfill-teams` had a real hole, and it is the reason this module covers it too.**
`ReferenceRepository.backfill_team_names` is fail-closed: a prefix collision or a code
with no name raises `TeamMappingError` and writes nothing. The Typer body caught
`SQLAlchemyError` and nothing else, so that refusal reached the terminal as a traceback —
on the screen of the one command whose entire job is to be the remedy for names that did
not resolve.

**And the remedy sentence has to be a different sentence.**
`scraping.resolve_team_names_or_report` tells a *scraper* to run
`fantabot db backfill-teams` later. Repeating that here would tell an operator to run the
command they are already running. What is actually missing is the season's full club
names, which live in `match_grain` and arrive with `db scrape voti`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fantabot.adapters.http import apileague
from fantabot.adapters.persistence.repositories.league import LeagueRepository
from fantabot.adapters.persistence.scraping import backfill_team_names
from fantabot.domain.shared.club_names import TeamMappingError
from fantabot.domain.shared.league import TeamSnapshot, parse_team_snapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from fantabot.adapters.tokens.store import TokenStore


class NamesUnresolved(RuntimeError):
    """The club-name mapping is not trustworthy, so nothing was written.

    Named apart from the driver errors a backfill can also raise, because the remedies
    have nothing in common: this one is a season to scrape, that one is a database to
    start. Both surfaces render it their own way — a `typer.Exit(2)` with the line
    printed, a 422 with the line in the body — without either having to tell the two
    apart for itself.
    """


def snapshot_team(session: Session, league_id: int, *, store: TokenStore) -> TeamSnapshot:
    """Capture our own team's credits and roster ids into `league_team_snapshot`.

    One authenticated read and one insert, on one session — deliberately unlike
    `lega_sync`, whose reads-and-writes-are-separate-phases rule exists because its pool
    read is several megabytes. This is one small GET, and the session is open for the
    token read regardless.

    Nothing is caught. There is no partial outcome to report — one read, one write — and
    a capture that silently did not happen leaves a permanent gap in an append-only
    table.
    """
    body = apileague.my_team(league_id, store=store)
    snapshot = parse_team_snapshot(league_id, body)
    LeagueRepository(session).record_team_snapshot(snapshot)
    return snapshot


def backfill_teams(session: Session) -> int:
    """Resolve `teams.nome_completo` from the two vocabularies already in Postgres.

    Returns how many rows changed. **Zero is an answer**, not a failure: a fresh database
    — or one scraped listone-first — has no `match_grain` names to resolve from, and the
    placeholder codes stay until fixtures exist.

    `TeamMappingError` becomes `NamesUnresolved`, carrying the original's own words plus
    the remedy. Everything else propagates: a driver error means nobody knows whether the
    mapping is trustworthy, and dressing it up as one would send the operator to scrape a
    season when the database is down.
    """
    try:
        return backfill_team_names(session)
    except TeamMappingError as exc:
        raise NamesUnresolved(
            f"club names not resolved ({exc}) — nothing was written. The full names come "
            "from `match_grain`, so scrape that season's voti first: "
            "`fantabot db scrape voti --season 2026/27`."
        ) from None


__all__ = ["NamesUnresolved", "backfill_teams", "snapshot_team"]
