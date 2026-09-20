"""Which listone a room is priced against, and who said so.

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

#: The two games. A string that is neither is not a third format to support — `asta room`
#: reached the same conclusion separately, as `RoomRefused`.
LISTONI = ("mantra", "classic")

#: Provenance, printed so an operator can tell a strong fact from a weak one. Defined here
#: rather than imported from `domain/asta/state.py`: that module's `OPERATOR_DECLARED` reads
#: "given with --size", which is a statement about a roster band and not about a format, and
#: `state.py` deliberately keeps its own three apart for the same reason.
OPERATOR_TYPED = "given with --format"
ROOM_DECLARED = "declared by the room"
RECORDED_CORPUS = "seen in the recorded corpus"


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
