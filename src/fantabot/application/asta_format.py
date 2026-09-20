"""Which listone a room is priced against, which recorded cell, and who said so.

`asta live --league` declared `--format mantra` as its default while its own help text
stated the harm: *"a Classic room read as Mantra is advised off players it cannot call,
priced off another game — and nothing raises."* It documented the hazard and defaulted
into it.

**Nothing raises because nothing can.** `asta_planner` reads
`reference.quotazioni(season, listone)` and `clearing_sales(asta_type=listone)`, and both
are legal and non-empty for the wrong format; the FantaLab listone bridge is
format-agnostic, so not even `EmptyPool` fires. The result is exit 0 and a complete,
confident advisory for another game — the same shape as the 2026-09-05 defect where a
Classic plan bought a 25-man roster for 25 credits of 500 because its price corpus was
silently empty.

Three rungs, most-specific first: what the operator typed, what the room itself declares,
then what the recorded corpus saw. **There is no fourth rung.** A run with no answer is
refused rather than defaulted, which is the whole point of the module — `AdvisoryRequest`
already documents `listone` as "not defaulted, and that is the whole point of it", and
this is the decision that lets the interface honour that.

Pure. It decides which answer wins; fetching one is the caller's problem.
"""

from __future__ import annotations

from dataclasses import dataclass

from fantabot.application.plan_request import DEFAULT_NUM_CREDITS, DEFAULT_NUM_TEAMS

#: The two games. A string that is neither is not a third format to support — `asta room`
#: reached the same conclusion separately, as `RoomRefused`.
LISTONI = ("mantra", "classic")

#: Provenance, printed so an operator can tell a strong fact from a weak one. Defined here
#: rather than imported from `domain/asta/state.py`: that module's `OPERATOR_DECLARED` reads
#: "given with --size", which is a statement about a roster band and not about a format, and
#: `state.py` deliberately keeps its own three apart for the same reason.
OPERATOR_TYPED = "given on the command line"
ROOM_DECLARED = "declared by the room"
RECORDED_CORPUS = "seen in the recorded corpus"

#: The shape's fourth answer, which the format deliberately does not have. See `choose_shape`.
ASSUMED_SHAPE = "assumed, nobody said"


class FormatUnknown(RuntimeError):
    """Nothing said which game this is, so nothing is assumed."""


@dataclass(frozen=True, slots=True)
class ResolvedFormat:
    """The listone to price against, where it came from, and anything worth saying aloud."""

    listone: str
    provenance: str
    #: Set only when what the operator typed contradicts something that was read. Never a
    #: refusal — an override is legal and is announced, exactly as `_lega_rules` announces
    #: one that disagrees with a lega.
    warning: str | None = None


def _known(value: str | None) -> str | None:
    return value if value in LISTONI else None


def choose_listone(typed: str, declared: str | None, recorded: str | None) -> ResolvedFormat:
    """`(listone, provenance, warning)` — or `FormatUnknown` when no rung answered.

    `typed` is `--format`, empty when it was not given. `declared` is the room's own
    `asta_type`; `recorded` is what the harvest corpus holds for the same id. Either may be
    `None` — unreachable, unauthenticated, or simply never harvested — and either may be a
    string neither game recognises, which is treated as no answer rather than as a third
    format.
    """
    read = _known(declared) or _known(recorded)

    if typed:
        if typed not in LISTONI:
            raise FormatUnknown(f"{typed!r} is not a format. Use 'mantra' or 'classic'.")
        warning = None
        if read is not None and read != typed:
            # The lega path says this in the same shape and for the same reason: a room
            # planned as something it is not is a thing to do deliberately or not at all.
            source = ROOM_DECLARED if _known(declared) else RECORDED_CORPUS
            warning = (
                f"this room is {read} ({source}); pricing it as {typed} because --format says so"
            )
        return ResolvedFormat(typed, OPERATOR_TYPED, warning)

    if _known(declared):
        return ResolvedFormat(declared or "", ROOM_DECLARED)
    if _known(recorded):
        # A weaker fact than the room's own word — harvested rooms are a population, and
        # this one is an id that happened to be in it — which is why the provenance is
        # printed rather than swallowed.
        return ResolvedFormat(recorded or "", RECORDED_CORPUS)

    raise FormatUnknown(
        "nothing says whether this room is mantra or classic, and the two are different "
        "games — the pool, the prices and the advisory all differ. Pass --format mantra "
        "or --format classic."
    )


@dataclass(frozen=True, slots=True)
class ResolvedShape:
    """The recorded corpus cell to price against, and where each half of it came from.

    Two provenances rather than one, because the halves resolve independently: `--teams 10`
    against a room that states its own credits is a legal and useful thing to type, and a
    single label would have to lie about one of them.
    """

    teams: int
    credits: int
    teams_from: str
    credits_from: str
    warning: str | None = None


def _stated(value: int | None) -> int | None:
    """A room's number, or `None` when it is not one.

    FantaLab sends `0` for a field it has not set. Taken literally that is a league of no
    teams with no credits, and `clearing_sales` would go looking for a `0x0` cell — a
    `NoCorpus` refusal whose message blames the operator for something the room did.
    """
    return value if value is not None and value > 0 else None


def choose_shape(
    typed_teams: int | None,
    typed_credits: int | None,
    declared_teams: int | None,
    declared_credits: int | None,
) -> ResolvedShape:
    """Which recorded cell to average prices from: what was typed, else the room, else 8x500.

    **There is a fourth rung here and deliberately none for the format**, which is the one
    asymmetry in this module worth stating. A format decides *which game is being played*
    and has no defensible default — reading a Classic room as Mantra is a complete,
    confident answer about another sport. A shape decides which recorded cell to average,
    8x500 is the corpus's dominant one, and `clearing_sales` raises `NoCorpus` underneath
    for a cell that was never recorded at all.

    That refusal is also why the silence matters. A 10x650 room left at 8x500 does **not**
    raise — 8x500 is a cell that exists and is full — so it is priced against somebody
    else's league with nothing said. The provenance is returned so the caller prints it: an
    assumed shape and a declared one are different facts about the same two numbers.
    """
    room_teams = _stated(declared_teams)
    room_credits = _stated(declared_credits)

    teams = typed_teams if typed_teams is not None else (room_teams or DEFAULT_NUM_TEAMS)
    credits = (
        typed_credits if typed_credits is not None else (room_credits or DEFAULT_NUM_CREDITS)
    )

    disagreements = []
    if typed_teams is not None and room_teams is not None and typed_teams != room_teams:
        disagreements.append(f"{room_teams} teams (--teams says {typed_teams})")
    if typed_credits is not None and room_credits is not None and typed_credits != room_credits:
        disagreements.append(f"{room_credits} credits (--credits says {typed_credits})")

    return ResolvedShape(
        teams=teams,
        credits=credits,
        teams_from=_shape_source(typed_teams, room_teams),
        credits_from=_shape_source(typed_credits, room_credits),
        warning=(
            f"this room declares {' and '.join(disagreements)}" if disagreements else None
        ),
    )


def _shape_source(typed: int | None, declared: int | None) -> str:
    if typed is not None:
        return OPERATOR_TYPED
    return ROOM_DECLARED if declared is not None else ASSUMED_SHAPE


def budget_for(typed: float, shape: ResolvedShape) -> float:
    """Our starting credits: what was typed, else the room's own.

    `0` means "ask the room", the idiom `asta room` already uses for `--budget`. Our credits
    in a 10x650 room are 650, and defaulting to 500 there is wrong in exactly the way
    defaulting the shape is — it is the same number read from the same field.
    """
    return typed if typed else float(shape.credits)
