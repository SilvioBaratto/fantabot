"""Players kept out of every plan — `db exclude` / `db exclusions`, beside the Asta page.

**One implementation, two printers.** `application/exclusions.py` decides what a valid
exclusion is and resolves each row's name; this module maps its types onto the wire and
decides nothing. That matters more here than on most screens, because an exclusion is
invisible everywhere else: it removes a player from every plan the bot makes and no plan
says so. A form that accepted what the command refuses would write rows that the
command's own list cannot then explain.

**The path follows the command, the tag follows the page.** `/db/exclusions` because
this is `fantabot db exclusions`, `tags=["asta"]` because the Asta page is where the
effect shows and where the operator is standing when they need it. Kept out of
`endpoints/db.py`, whose subject is the health probe and whose "must never 500" rule is
the opposite of the one the write below needs.

**The two routes sit on opposite sides of `api/outcomes.py`'s rule.**

* The list is a status read, so it **degrades open** — but never into a bare empty list.
  `[]` with no error is the true answer for a fresh install ("every player on the listone
  is buyable"), and `[]` because Postgres would not open is a different fact with a
  different remedy. `error` is what keeps them apart, and it is the whole lesson of the
  `found=false` screens this phase removed.
* The write is a decision, so it **fails closed**. `InvalidExclusion` becomes a 422
  carrying the refusal's own wording; nothing else is caught. `auth.py`'s two disconnects
  reached the same conclusion for a sharper reason: `get_session` commits on clean exit,
  i.e. *inside* any `try` wrapped around it, so a degrade-open handler would turn a
  rolled-back write into a 200 saying the row was recorded.

**There is no delete, and its absence is deliberate.** `fantabot` has no un-exclude
command, and `SPEC.md` §8 Never #4 is explicit that the app gets no power the CLI lacks —
the CLI gets the command first. Recorded in `tasks/todo.md` rather than built here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from fantabot_app.api.outcomes import because

router = APIRouter()


class Exclusion(BaseModel):
    """One row of the list. Named apart from `exclusions.ExclusionRow` on purpose — one
    is the decision, one is its wire format, and collapsing them is how a response model
    starts deciding things."""

    player_id: int
    #: The player's name on `players`, or `null` when this database has never scraped
    #: that id. `null` rather than `"?"` or `""`: an id nothing resolves is either a typo
    #: to delete or a season to scrape, and the page renders those differently.
    nome: str | None
    reason: str
    source: str


class Exclusions(BaseModel):
    """The list. `error` is set only when the list could not be read at all."""

    exclusions: list[Exclusion]
    #: Why the list is empty for a reason other than being empty. `null` on success,
    #: including on a genuinely empty table.
    error: str | None = None


class ExcludeRequest(BaseModel):
    """What the form sends. No validation here beyond the types: what makes an exclusion
    valid is `application/exclusions.clean_exclusion`'s, and a `min_length=1` on `reason`
    would be a second copy of it — one that refuses with a different message, in a
    different field, and that the command would not share."""

    player_id: int
    reason: str
    source: str = Field(default="")


class ExclusionWritten(BaseModel):
    """What a write leaves behind: the row, and the list it is now part of."""

    recorded: Exclusion
    #: The refreshed list, so the page renders what the table now holds rather than what
    #: it hopes it holds. An upsert over an existing id replaces the reason in place.
    exclusions: list[Exclusion]
    #: What `db exclude` prints as "N exclusions in total".
    total: int


def to_wire(row: Any) -> Exclusion:
    """Map an `ExclusionRow` onto the response (pure; unit-testable without a Session).

    `Any` rather than the dataclass, on `endpoints/system.py::build_config`'s precedent:
    `fantabot` ships no `py.typed`, so this venv's mypy reads every symbol from it as
    `Any` anyway, and naming the type here would buy an import and no checking.
    """
    return Exclusion(
        player_id=row.player_id,
        nome=row.nome,
        reason=row.reason,
        source=row.source,
    )


@router.get("/db/exclusions", response_model=Exclusions, tags=["asta"])
def list_exclusions() -> Exclusions:
    """What `fantabot db exclusions` prints, for the Asta page."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.exclusions import read_exclusions

    try:
        with database_manager.get_session() as session:
            return Exclusions(exclusions=[to_wire(row) for row in read_exclusions(session)])
    except SQLAlchemyError as exc:
        return Exclusions(exclusions=[], error=because(exc))


@router.post(
    "/db/exclusions", response_model=ExclusionWritten, status_code=201, tags=["asta"]
)
def record(request: ExcludeRequest) -> ExclusionWritten:
    """Keep a player out of every asta plan — `fantabot db exclude`, from the browser.

    The arguments cross untouched: not a trim, not a default source. The one place that
    decides what a valid exclusion is has to be the one place, or the browser and the
    terminal refuse different things.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.exclusions import InvalidExclusion, record_exclusion

    try:
        with database_manager.get_session() as session:
            recorded = record_exclusion(
                session,
                request.player_id,
                reason=request.reason,
                source=request.source,
            )
    except InvalidExclusion as exc:
        # The refusal's own wording, not a restatement: the operator reading this
        # sentence on a page and the one reading it in a terminal are the same person.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return ExclusionWritten(
        recorded=to_wire(recorded.row),
        exclusions=[to_wire(row) for row in recorded.exclusions],
        total=recorded.total,
    )
