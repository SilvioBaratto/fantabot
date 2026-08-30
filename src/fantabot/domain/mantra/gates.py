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

from fantabot.domain.mantra.models import (
    CELL_VALUES,
    ROLE_ORDER,
    CompatMatrix,
    FormationCompat,
    MantraSchema,
    SchemaGrid,
)
from fantabot.domain.news.mantra import MANTRA_CODES

_ROLE_INDEX = {code.upper(): i for i, code in enumerate(ROLE_ORDER)}

EXPECTED_SCHEMI = 11
OUTFIELD_SLOTS = 10
# rules/sistema-mantra.md speaks of slots listing "two roles", and this gate
# turned that phrasing into a ceiling of two. It is not one: the published table
# gives 4-3-1-2 a slot of three, `T/A/Pc`, and the cap silently truncated it to
# `A/Pc` in the artefact L1 depends on. Four would be beyond anything observed;
# three is what the source actually prints.
MAX_ROLES_PER_SLOT = 3

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
    """Every problem with the compatibility matrix, judged against the grid.

    These gates exist because the previous ones could not tell a collected table
    from an echo. The only substantive check was that 4-1-4-1 carried the W/T
    exception — a fact the prompt itself supplied — so a run that found nothing
    and handed the example back passed everything, and the file that shipped held
    one entry and ten empty lists for a year.

    What a real table cannot fake: a row per slot of every schema, twelve cells
    per row, each cell one of four values, a slot accepting its own role without
    malus, and a keeper column no outfield slot will take.
    """
    problems: list[str] = []

    covered = {entry.schema_nome for entry in matrix.formazioni}
    expected = {schema.nome for schema in grid.schemi}

    for missing in sorted(expected - covered):
        problems.append(f"compatibility matrix has no entry for schema {missing!r}")
    for unknown in sorted(covered - expected):
        problems.append(f"compatibility matrix names schema {unknown!r}, which the grid lacks")

    if [code.upper() for code in matrix.ruoli] != [code.upper() for code in ROLE_ORDER]:
        problems.append(
            f"compatibility matrix columns are {matrix.ruoli!r}; every row is "
            f"positional, so the order must be exactly {list(ROLE_ORDER)!r}"
        )

    by_name = {schema.nome: schema for schema in grid.schemi}
    for entry in matrix.formazioni:
        problems.extend(_check_formation(entry, by_name.get(entry.schema_nome)))

    if not matrix.fonti:
        problems.append(
            "compatibility matrix carries no `fonti`: without the URLs actually "
            "read there is no way to tell a real collection from the prompt's own "
            "worked example handed back"
        )

    problems.extend(_check_named_exception(matrix))
    return problems


def _check_formation(entry: FormationCompat, schema: MantraSchema | None) -> list[str]:
    """One schema's rows, against the slots the grid says it has."""
    problems: list[str] = []
    name = entry.schema_nome

    if not entry.slots:
        problems.append(
            f"{name}: no rows. An entry naming a schema and describing none of its "
            f"slots is the shape the old `vietati`-only matrix had, and it cannot "
            f"answer whether a rosa can field the schema"
        )
        return problems

    for row in entry.slots:
        if len(row.compat) != len(ROLE_ORDER):
            problems.append(
                f"{name}: slot {row.slot!r} has {len(row.compat)} cells, "
                f"expected {len(ROLE_ORDER)}"
            )
            continue
        for code, value in zip(ROLE_ORDER, row.compat, strict=True):
            if value not in CELL_VALUES:
                problems.append(
                    f"{name}: slot {row.slot!r} column {code} is {value!r}, "
                    f"not one of {sorted(CELL_VALUES)}"
                )
        # A slot must take the role it is named for, at no cost. This is the
        # cheapest check that a row is a transcription rather than a guess.
        for own in _slot_codes(row.slot):
            if own not in MANTRA_CODES:
                problems.append(f"{name}: slot {row.slot!r} names {own!r}, not a Mantra code")
                continue
            value = row.compat[_ROLE_INDEX[own]]
            if value != "ok":
                problems.append(
                    f"{name}: slot {row.slot!r} does not accept its own role "
                    f"{own} without malus ({value!r})"
                )

    labels = [row.slot for row in entry.slots]
    if not labels or _slot_codes(labels[0]) != {"POR"}:
        problems.append(f"{name}: first row is {labels[:1]!r}; the keeper's row comes first")

    for row in entry.slots[1:]:
        if len(row.compat) == len(ROLE_ORDER) and row.compat[_ROLE_INDEX["POR"]] != "no":
            problems.append(
                f"{name}: outfield slot {row.slot!r} accepts a Por "
                f"({row.compat[_ROLE_INDEX['POR']]!r}); the keeper is never an outfielder"
            )

    if schema is not None:
        want = ["/".join(slot).upper() for slot in schema.slots]
        got = [row.slot.upper() for row in entry.slots[1:]]
        if want != got:
            problems.append(
                f"{name}: rows do not match the grid's slots\n"
                f"    grid:   {want}\n"
                f"    matrix: {got}"
            )
    return problems


def _slot_codes(label: str) -> set[str]:
    """``'T/A/Pc'`` -> ``{'T', 'A', 'PC'}``."""
    return {part.strip().upper() for part in label.split("/") if part.strip()}


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

    # In the full matrix the exception is not a pair in a list, it is cells: a
    # slot named for T must refuse a W outright, and vice versa. Reading it from
    # the grid rather than from a declaration means the check cannot be satisfied
    # by echoing the prompt — the surrounding 143 cells have to be there too.
    blocked = []
    for row in entry.slots:
        if len(row.compat) != len(ROLE_ORDER):
            continue
        codes = _slot_codes(row.slot)
        if "T" in codes and "W" not in codes:
            blocked.append(("T-slot", "W", row.compat[_ROLE_INDEX["W"]]))
        if "W" in codes and "T" not in codes:
            blocked.append(("W-slot", "T", row.compat[_ROLE_INDEX["T"]]))

    wrong = [one for one in blocked if one[2] != "no"]
    if not blocked or wrong:
        return [
            f"{NAMED_EXCEPTION_SCHEMA}: the W/T exception is missing or wrong "
            f"({wrong or 'no T or W slot found at all'}). rules/sistema-mantra.md "
            f"names it explicitly — W and T are normally interchangeable with a -1 "
            f"malus, but never in this schema, not even with one. Losing it "
            f"silently permits an illegal substitution."
        ]
    return []
