"""The two one-shot team commands — `db snapshot-team` and `db backfill-teams`.

**One implementation, two printers.** `application/team_maintenance.py` chooses which
endpoint says what our own credits are, which function translates its abbreviated keys,
and which repository method writes the row; this module maps its result onto the wire and
decides nothing. Three choices a second surface would otherwise have had to guess — and
`league_team_snapshot` is append-only, so a row stored under the wrong lega stays.

**The path follows the command, the tag follows the page.** `/db/snapshot-team` and
`/db/backfill-teams` because that is what the two commands are called; `tags=["lega"]`
because Synchronize is where they belong and the lega is that page's subject. Kept out of
`endpoints/db.py` for `exclusions.py`'s reason: that module's subject is the health probe,
whose "must never 500" rule is the opposite of the one a write needs.

**Both are writes, so both fail closed** (`api/outcomes.py`). A failure is a named outcome
carrying its own remedy, never a 200 that reads like success — for `snapshot-team` because
a capture that silently did not happen leaves a permanent gap in an append-only table, and
for `backfill-teams` because the refusal it actually has (`NamesUnresolved`, a club code
that resolves to no name) means nothing was written and a season has to be scraped.

Neither is a job. `api/infrastructure/jobs.py` is for the long and the interactive — a
login, an eight-read sync, a news fan-out. These are one small GET plus an insert, and one
SQL pass; `GET /lineup/plan` already reads the live platform inside a request.

**Not a request-validation refusal, so not an `HTTPException`.** `endpoints/exclusions.py`
answers 422 because a blank reason is a sentence the operator has to rewrite. Nothing here
is about the shape of the request: the answers are about a credential, a network and the
state of the database, which is `endpoints/room.py`'s situation and gets its idiom.
"""

from __future__ import annotations

from typing import Any

from fantabot.application.team_maintenance import (
    NamesUnresolved,
    backfill_teams,
    snapshot_team,
)
from fantabot.domain.tokens.errors import (
    ApiTimeout,
    ApiUnavailable,
    AppKeyRejected,
    TokenError,
    TokenRejected,
)
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from fantabot_app.api.outcomes import because

router = APIRouter()

#: `POST /db/snapshot-team`. `refused` is the platform answering and saying no, which is
#: a different fact from never having been able to ask — the remedy for one is a new
#: login, for the other it is waiting.
SNAPSHOT_OUTCOMES = ("saved", "no_credential", "refused", "unreachable")

#: `POST /db/backfill-teams`. `unresolved` is not an outage: the mapping is fail-closed,
#: nothing was written, and the remedy is a scrape. Folding it into `unreachable` would
#: send the operator to start a database that is already running.
BACKFILL_OUTCOMES = ("resolved", "unresolved", "unreachable")


class SnapshotRequest(BaseModel):
    """Which lega to capture. Required, and never defaulted from settings.

    The CLI falls back to `FANTABOT_LEAGUE_ID` because a Typer option needs a default to
    be optional. A page has a field, the operator is looking at it, and a silent fallback
    to a lega they did not pick writes a row into an append-only table.
    """

    league_id: int


class TeamSnapshotResult(BaseModel):
    """What was captured, or why nothing was."""

    outcome: str
    #: Empty on `saved`. The refusal's own words otherwise — the operator reading this on
    #: a page and the one reading it in a terminal are the same person.
    reason: str = ""
    league_id: int
    team_id: int | None = None
    nome: str = ""
    owner: str = ""
    #: `null` rather than `0` when the platform did not send the figure. The CLI prints
    #: `or 0` because a terminal line needs a number; a `0` in a JSON field is a claim
    #: that the team has spent nothing.
    credits_initial: int | None = None
    credits_spent: int | None = None
    credits_remaining: int | None = None


class BackfillResult(BaseModel):
    """How many club names were resolved, or why none were."""

    outcome: str
    reason: str = ""
    #: Rows whose `nome_completo` changed. **Zero is a success**: a fresh database, or one
    #: scraped listone-first, has no `match_grain` names to resolve from yet.
    changed: int = 0


def to_wire(snapshot: Any) -> TeamSnapshotResult:
    """Map a `TeamSnapshot` onto the response (pure; unit-testable without a Session).

    `Any` rather than the dataclass, on `endpoints/system.py::build_config`'s precedent:
    `fantabot` ships no `py.typed`, so this venv's mypy reads every symbol from it as
    `Any` anyway, and naming the type here would buy an import and no checking.
    """
    return TeamSnapshotResult(
        outcome="saved",
        league_id=snapshot.league_id,
        team_id=snapshot.team_id,
        nome=snapshot.nome,
        owner=snapshot.owner,
        credits_initial=snapshot.credits_initial,
        credits_spent=snapshot.credits_spent,
        credits_remaining=snapshot.credits_remaining,
    )


@router.post("/db/snapshot-team", response_model=TeamSnapshotResult, tags=["lega"])
def snapshot(request: SnapshotRequest) -> TeamSnapshotResult:
    """Capture our own team's credits and roster ids — `fantabot db snapshot-team`.

    One authenticated read of `GET /onboarding/v1/league/teams/my` and one insert. Every
    call appends a **new** row; a re-run never overwrites the last capture, which is what
    makes the drift between two captures readable at all.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher

    key = settings.fantabot_encryption_key
    if not key:
        # Asked anyway, this is a `KeyMissing` whose wording is about a key file. The
        # operator's actual next move is to connect an account.
        return TeamSnapshotResult(
            outcome="no_credential",
            reason="No encryption key set — connect an account first.",
            league_id=request.league_id,
        )

    try:
        cipher = TokenCipher(key)
        with database_manager.get_session() as session:
            captured = snapshot_team(
                session, request.league_id, store=TokenStore(session, cipher)
            )
    except (TokenRejected, AppKeyRejected) as exc:
        return TeamSnapshotResult(
            outcome="refused", reason=str(exc), league_id=request.league_id
        )
    except (ApiTimeout, ApiUnavailable) as exc:
        return TeamSnapshotResult(
            outcome="unreachable", reason=str(exc), league_id=request.league_id
        )
    except TokenError as exc:
        return TeamSnapshotResult(
            outcome="no_credential", reason=str(exc), league_id=request.league_id
        )
    except (SQLAlchemyError, OSError) as exc:
        return TeamSnapshotResult(
            outcome="unreachable", reason=because(exc), league_id=request.league_id
        )

    return to_wire(captured)


@router.post("/db/backfill-teams", response_model=BackfillResult, tags=["lega"])
def backfill() -> BackfillResult:
    """Resolve club codes to full names — `fantabot db backfill-teams`.

    No arguments, as the command has none: the backfill reads the two vocabularies
    already in Postgres and resolves whatever it can. The scrapers print an instruction
    to run this when a season's `teams` rows have codes but no names.
    """
    from fantabot.adapters.persistence import database_manager

    try:
        with database_manager.get_session() as session:
            changed = backfill_teams(session)
    except NamesUnresolved as exc:
        return BackfillResult(outcome="unresolved", reason=str(exc))
    except (SQLAlchemyError, OSError) as exc:
        return BackfillResult(outcome="unreachable", reason=because(exc))

    return BackfillResult(outcome="resolved", changed=changed)
