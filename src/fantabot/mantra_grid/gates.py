"""Fail-closed checks on a collected grid. Pure: no SDK, no filesystem, no network.

Every function returns a list of problems. Empty means the grid may be written;
anything else means it may not, and the caller prints the list rather than
writing a file. A collected artefact is never hand-patched past a gate — re-run
the collector, or fix the gate if the gate is what is wrong.

The stakes are why: rules/sistema-mantra.md publishes the full compatibility
table as a separate download this repo has no local copy of, so nothing else here
can catch a mis-transcription. A wrong matrix produces lineups the platform
silently rejects or penalises, every week, for a season.
"""

from __future__ import annotations

from ..news.mantra import MANTRA_CODES
from .models import CompatMatrix, MantraSchema, SchemaGrid

EXPECTED_SCHEMI = 11
OUTFIELD_SLOTS = 10
# rules/sistema-mantra.md: "Where a schema slot lists two roles, they're
# interchangeable alternatives." Two is the ceiling; a third is the collector
# going beyond its source.
MAX_ROLES_PER_SLOT = 2

# rules/sistema-mantra.md: every schema fields exactly five of each profile.
DEFENSIVE_PROFILE: frozenset[str] = frozenset({"DD", "DS", "DC", "B", "E", "M"})
OFFENSIVE_PROFILE: frozenset[str] = frozenset({"C", "T", "W", "A", "PC"})

# The one exception the rules name explicitly: W and T are normally interchangeable
# with a -1 malus, except here, where the swap is impossible at any price.
NAMED_EXCEPTION_SCHEMA = "4-1-4-1"
NAMED_EXCEPTION_PAIR = ("W", "T")


def check_schemi(grid: SchemaGrid) -> list[str]:
    """Every problem with the schema grid, not just the first.

    A collector run is expensive; reporting one problem per attempt would turn
    fixing a bad transcription into an N-round trip.
    """
    problems: list[str] = []

    if len(grid.schemi) != EXPECTED_SCHEMI:
        problems.append(
            f"expected {EXPECTED_SCHEMI} schemas, got {len(grid.schemi)}: "
            f"{[s.nome for s in grid.schemi]}"
        )

    names = [s.nome for s in grid.schemi]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        problems.append(f"duplicate schema names: {', '.join(duplicates)}")

    for schema in grid.schemi:
        problems.extend(_check_one(schema))

    return problems


def check_compat(matrix: CompatMatrix, grid: SchemaGrid) -> list[str]:
    """Every problem with the compatibility matrix, judged against the grid."""
    problems: list[str] = []

    covered = {entry.schema_nome for entry in matrix.formazioni}
    expected = {schema.nome for schema in grid.schemi}

    for missing in sorted(expected - covered):
        problems.append(f"compatibility matrix has no entry for schema {missing!r}")
    for unknown in sorted(covered - expected):
        problems.append(f"compatibility matrix names schema {unknown!r}, which the grid lacks")

    for entry in matrix.formazioni:
        for pair in entry.vietati:
            if len(pair) != 2:
                problems.append(f"{entry.schema_nome}: blocked pair {pair!r} is not [from, to]")
                continue
            for code in pair:
                if code.upper() not in MANTRA_CODES:
                    problems.append(f"{entry.schema_nome}: {code!r} is not a Mantra role code")

    if not matrix.fonti:
        problems.append(
            "compatibility matrix carries no `fonti`: without the URLs actually "
            "read there is no way to tell a real collection from the prompt's own "
            "worked example handed back"
        )

    problems.extend(_check_named_exception(matrix))
    return problems


def _check_one(schema: MantraSchema) -> list[str]:
    problems: list[str] = []

    if len(schema.slots) != OUTFIELD_SLOTS:
        problems.append(
            f"{schema.nome}: expected {OUTFIELD_SLOTS} outfield slots, got {len(schema.slots)}"
        )

    for slot in schema.slots:
        if not slot:
            problems.append(f"{schema.nome}: empty slot")
            continue
        if len(slot) > MAX_ROLES_PER_SLOT:
            problems.append(
                f"{schema.nome}: slot {'/'.join(slot)} lists {len(slot)} roles; "
                f"the rules allow at most {MAX_ROLES_PER_SLOT} interchangeable alternatives"
            )
        for code in slot:
            upper = code.upper()
            if upper not in MANTRA_CODES:
                problems.append(f"{schema.nome}: {code!r} is not a Mantra role code")
            elif upper == "POR":
                problems.append(
                    f"{schema.nome}: POR appears among the outfield slots; the "
                    f"goalkeeper is fixed and sits outside the four lines"
                )

    if not problems and not _profiles_can_split_five_five(schema):
        problems.append(
            f"{schema.nome}: slots cannot be split into 5 defensive-profile "
            f"(Dd Ds Dc B E M) and 5 offensive-profile (C T W A Pc) players"
        )

    return problems


def _profiles_can_split_five_five(schema: MantraSchema) -> bool:
    """Is there *an* assignment of slots to profiles giving exactly 5 and 5?

    Checking that each slot is unambiguously one profile would be wrong: real
    schemas list interchangeable alternatives, and a slot offering M or C spans
    both. So this asks whether a valid split exists, not whether it is forced.
    """
    must_defend = 0
    can_defend = 0
    for slot in schema.slots:
        codes = {code.upper() for code in slot}
        defensive = bool(codes & DEFENSIVE_PROFILE)
        offensive = bool(codes & OFFENSIVE_PROFILE)
        if defensive:
            can_defend += 1
        if defensive and not offensive:
            must_defend += 1
        if not defensive and not offensive:
            return False
    return must_defend <= 5 <= can_defend


def _check_named_exception(matrix: CompatMatrix) -> list[str]:
    entry = next((e for e in matrix.formazioni if e.schema_nome == NAMED_EXCEPTION_SCHEMA), None)
    if entry is None:
        return []  # already reported as a missing schema

    pairs = {tuple(code.upper() for code in pair) for pair in entry.vietati if len(pair) == 2}
    if NAMED_EXCEPTION_PAIR not in pairs and NAMED_EXCEPTION_PAIR[::-1] not in pairs:
        return [
            f"{NAMED_EXCEPTION_SCHEMA}: the W/T exception is missing. "
            f"rules/sistema-mantra.md names it explicitly — W and T are normally "
            f"interchangeable with a -1 malus, but never in this schema, not even "
            f"with one. Losing it silently permits an illegal substitution."
        ]
    return []
