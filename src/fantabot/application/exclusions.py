"""A player kept out of every plan — the list, and the acts of adding and removing.

`adapters/persistence/models/exclusions.py` says what the table is for and why nothing
else in the engine can do its job: the scraper faithfully reproduces a site that lags
reality, and the sentiment gate is floored at `disp_floor=0.50` / `tit_floor=0.40` so
that news tilts a value and never vetoes it. This module is the decision layer over that
table, and it exists because the two Typer bodies held decisions the Asta page needs and
could otherwise only have got by writing them a second time.

**The name is what makes the list readable.** `db exclusions` ran
`SELECT id, nome FROM players WHERE id = ANY(:ids)` inside the command body — a join
written in `interface/`, where the app cannot reach it. `1234  left Serie A` and
`1234  Leao  left Serie A` answer different questions six months later, and "why is this
id here" is the only question anyone will have.

**A missing name is `None`, never a placeholder.** The command rendered the absent id and
the unknown one alike, as `?`. They are different facts with different remedies: an id
`players` has never carried is either a typo to delete or a season nobody has scraped.
The surfaces render that their own way; this layer states it.

**Two refusals, and they belong here rather than on either surface.** A blank reason and
a non-positive id each write a row that cannot be acted on later, and neither surface
catches them alone: `reason: str = typer.Option(...)` refuses a *missing* reason and
accepts `--reason ""`, and a JSON body has no Typer at all. One exclusion removes a
player from every plan the bot makes, silently — there is no second screen on which an
operator discovers it happened.

**Nothing here commits.** `exclude_player` does not, `database_manager.get_session`
commits on clean exit, and the command commits explicitly besides. A function that
committed on its caller's behalf would take the transaction boundary away from the one
place — the route, the command — that knows what else is in it.

**A removal validates nothing, deliberately.** `clean_exclusion` refuses a non-positive
id, and before T24 `db exclude` validated nothing at all — so a `0` row is exactly what
`remove_exclusion` is the remedy for. Checking the id on the way out would make the one
row nobody can act on permanent. What it does refuse is a removal that matched nothing:
`ExclusionNotFound` rather than a silent success, because "the row is gone" and "the row
was never there" send the operator to two different places.

Split in two like `asta_planner`: `clean_exclusion` and `join_names` are pure and take
rows; `read_exclusions`, `record_exclusion` and `remove_exclusion` are the shell over a
`Session`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class InvalidExclusion(ValueError):
    """The exclusion as asked for cannot be recorded. Named so each surface says so
    its own way — a `typer.Exit(2)` with the line printed, a 422 with the line in the
    body — without either having to tell this apart from a database being down.
    """


class ExclusionNotFound(LookupError):
    """Nothing is excluded under that id. Kept apart from `InvalidExclusion` because the
    surfaces answer them differently — a 404 against a 422 — and because the remedies
    differ: one is a sentence to rewrite, the other an id to look up on the list.
    """


@dataclass(frozen=True)
class ExclusionRow:
    """One row of the list, as both surfaces render it."""

    player_id: int
    #: The player's name on `players`, or `None` when this database has never scraped
    #: that id. A real fact, and the one that tells a typo from a missing season.
    nome: str | None
    #: Free text, for a human. What happened and when — never "excluded".
    reason: str
    #: Where the claim came from. Empty is legal and stays empty; the command has
    #: always had `--source` optional and the reason is the required half.
    source: str


@dataclass(frozen=True)
class ExclusionRecorded:
    """What a write leaves behind: the row, and the list it is now part of."""

    row: ExclusionRow
    #: Every exclusion after the write, in the table's own order — so a surface that
    #: has just recorded one renders the new list without asking again.
    exclusions: tuple[ExclusionRow, ...]

    @property
    def total(self) -> int:
        """What `db exclude` prints as "N exclusions in total"."""
        return len(self.exclusions)


def clean_exclusion(player_id: int, *, reason: str, source: str) -> tuple[int, str, str]:
    """The validated triple, or `InvalidExclusion`. Pure.

    Trimmed because a trailing newline is what a textarea sends; otherwise untouched,
    because the reason is free text for a human and this is not its editor.
    """
    if player_id <= 0:
        raise InvalidExclusion(
            f"{player_id} is not a player id — platform ids are positive. A row keyed "
            "on it would match nothing for ever while reading on the list as an "
            "exclusion that is doing something."
        )
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise InvalidExclusion(
            "an exclusion needs a reason. This removes the player from every plan the "
            "bot makes, and a row with no provenance is indistinguishable from a typo."
        )
    return player_id, cleaned_reason, source.strip()


def join_names(
    rows: Sequence[tuple[int, str, str]], names: Mapping[int, str]
) -> list[ExclusionRow]:
    """`repo.exclusions()` with each id's name attached. Pure, and order-preserving.

    `exclusions()` orders by `player_id`; the name lookup is a mapping and has no order
    of its own, so the rows lead. An id absent from `names` gets `None` — see the
    module docstring.
    """
    return [
        ExclusionRow(
            player_id=player_id,
            nome=names.get(player_id),
            reason=reason,
            source=source,
        )
        for player_id, reason, source in rows
    ]


def read_exclusions(session: Session) -> list[ExclusionRow]:
    """The list, named. Two reads on one session."""
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    repo = ReferenceRepository(session)
    rows = repo.exclusions()
    return join_names(rows, repo.player_names([player_id for player_id, _, _ in rows]))


def record_exclusion(
    session: Session, player_id: int, *, reason: str, source: str = ""
) -> ExclusionRecorded:
    """Validate, upsert, and read the list back. Does not commit — see the module docstring.

    Read back rather than assembled from the arguments, so the confirmation an operator
    sees is what the table now holds: an upsert over an existing id replaces the reason,
    and a name resolved here is the check that the id was the intended one.
    """
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    player_id, reason, source = clean_exclusion(player_id, reason=reason, source=source)
    ReferenceRepository(session).exclude_player(player_id, reason=reason, source=source)

    exclusions = read_exclusions(session)
    recorded = next(row for row in exclusions if row.player_id == player_id)
    return ExclusionRecorded(row=recorded, exclusions=tuple(exclusions))


@dataclass(frozen=True)
class ExclusionRemoved:
    """What a removal leaves behind: the row that is gone, and the list without it."""

    #: The removed row, whole. The reason is the only part of it nothing else in the
    #: database holds — the id was typed and the name is on `players` — so a surface
    #: that drops it makes the removal unreversible.
    row: ExclusionRow
    #: Every exclusion that remains, in the table's own order.
    exclusions: tuple[ExclusionRow, ...]

    @property
    def total(self) -> int:
        """What the surfaces print as "N exclusions in total"."""
        return len(self.exclusions)


def remove_exclusion(session: Session, player_id: int) -> ExclusionRemoved:
    """Withdraw one exclusion, or `ExclusionNotFound`. Does not commit.

    Reads before it deletes because the row's own reason is what the caller has to show
    — and because that read is how a removal that matched nothing is told from one that
    did. See the module docstring for why the id itself is not validated.
    """
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    before = read_exclusions(session)
    row = next((row for row in before if row.player_id == player_id), None)
    if row is None:
        raise ExclusionNotFound(
            f"no exclusion for id {player_id} — nothing was removed. `db exclusions` "
            "lists what is there."
        )

    ReferenceRepository(session).unexclude_player(player_id)
    # Read back rather than filtered out of `before`, for `record_exclusion`'s reason:
    # what the operator is shown is what the table now holds, not what this function
    # believes it did.
    return ExclusionRemoved(row=row, exclusions=tuple(read_exclusions(session)))
