"""T10: the gates that stand between a mis-transcribed grid and the whole season.

rules/sistema-mantra.md gives the general out-of-position rules in prose but says
outright that the full per-formation compatibility table "is a separate download
that isn't captured here". So the collector has to fetch something this repo
cannot check against a local copy — which is exactly when a gate earns its keep.

A gate with no failing fixture is not a gate, so every invariant here has one.
"""

import json

import pytest

from fantabot.mantra_grid.gates import check_compat, check_schemi
from fantabot.mantra_grid.models import (
    ROLE_ORDER,
    CompatMatrix,
    FormationCompat,
    MantraSchema,
    SchemaGrid,
    SlotCompat,
)

# 5 defensive-profile (Dd Ds Dc B E M) + 5 offensive-profile (C T W A Pc).
_WELL_FORMED_SLOTS = [
    ["DC"],
    ["DC"],
    ["DC"],
    ["DD", "E"],
    ["DS", "E"],
    ["C"],
    ["C"],
    ["T"],
    ["W"],
    ["PC"],
]

ELEVEN = [
    "3-4-3",
    "3-4-1-2",
    "3-5-2",
    "4-3-3",
    "4-4-2",
    "4-2-3-1",
    "4-1-4-1",
    "4-3-1-2",
    "5-3-2",
    "5-4-1",
    "4-5-1",
]


def _schema(nome: str = "4-3-3", slots: list[list[str]] | None = None) -> MantraSchema:
    return MantraSchema(nome=nome, slots=slots or [list(s) for s in _WELL_FORMED_SLOTS])


def _grid(names: list[str] | None = None, **overrides: object) -> SchemaGrid:
    return SchemaGrid(schemi=[_schema(n) for n in (names or ELEVEN)], **overrides)


def _row(label: str, blocked_for: dict[str, str] | None = None) -> SlotCompat:
    """A slot row: its own roles `ok`, a Por never, everything else `-1`."""
    own = {part.strip().upper() for part in label.split("/")}
    cells = []
    for code in ROLE_ORDER:
        upper = code.upper()
        if upper in own:
            cells.append("ok")
        elif upper == "POR":
            cells.append("no")
        else:
            cells.append((blocked_for or {}).get(upper, "-1"))
    return SlotCompat(slot=label, compat=cells)


def _keeper() -> SlotCompat:
    return SlotCompat(slot="Por", compat=["ok"] + ["no"] * (len(ROLE_ORDER) - 1))


def _formation(nome: str, exception: bool = True) -> FormationCompat:
    """One schema's rows, matching `_WELL_FORMED_SLOTS`.

    In 4-1-4-1 the T and W slots refuse each other outright, which is the one
    exception the rules name — and, in this shape, four cells rather than a
    declaration.
    """
    block = {"T": "no"} if (nome == "4-1-4-1" and exception) else {}
    rows = [_keeper()]
    for slot in _WELL_FORMED_SLOTS:
        label = "/".join(slot)
        if label == "T" and exception and nome == "4-1-4-1":
            rows.append(_row(label, {"W": "no"}))
        elif label == "W":
            rows.append(_row(label, block))
        else:
            rows.append(_row(label))
    return FormationCompat(schema_nome=nome, slots=rows)


def _matrix(names: list[str] | None = None, exception: bool = True) -> CompatMatrix:
    return CompatMatrix(
        ruoli=list(ROLE_ORDER),
        formazioni=[_formation(n, exception) for n in (names or ELEVEN)],
        fonti=["https://www.fantacalcio.it/regolamenti/sistema-mantra"],
    )


# --- schemi --------------------------------------------------------------


def test_a_well_formed_grid_passes() -> None:
    assert check_schemi(_grid()) == []


def test_ten_schemas_are_rejected() -> None:
    problems = check_schemi(_grid(ELEVEN[:10]))

    assert problems
    assert any("11" in p for p in problems)


def test_twelve_schemas_are_rejected() -> None:
    assert check_schemi(_grid([*ELEVEN, "3-3-4"])) != []


def test_a_schema_with_nine_outfield_slots_is_rejected() -> None:
    grid = _grid()
    grid.schemi[3] = _schema("4-3-3", [list(s) for s in _WELL_FORMED_SLOTS[:9]])

    problems = check_schemi(grid)

    assert any("4-3-3" in p and "10" in p for p in problems)


def test_a_six_four_profile_split_is_rejected() -> None:
    # rules/sistema-mantra.md: every schema fields exactly five defensive-profile
    # and five offensive-profile players. A 6/4 grid would field an illegal XI.
    # M is defensive-profile, so this fields six defenders and four attackers.
    six_four = [
        ["DC"],
        ["DC"],
        ["DC"],
        ["DD"],
        ["DS"],
        ["M"],
        ["C"],
        ["T"],
        ["W"],
        ["PC"],
    ]
    grid = _grid()
    grid.schemi[0] = _schema("3-4-3", six_four)

    problems = check_schemi(grid)

    assert any("3-4-3" in p for p in problems)


def test_a_slot_that_can_go_either_way_still_satisfies_the_split() -> None:
    # A slot listing M and C spans both profiles. The gate must check that SOME
    # assignment gives 5/5, not that every slot is unambiguous — real schemas
    # list interchangeable alternatives.
    flexible = [
        ["DC"],
        ["DC"],
        ["DD", "E"],
        ["DS", "E"],
        ["M", "C"],
        ["M", "C"],
        ["C"],
        ["T", "W"],
        ["W"],
        ["PC"],
    ]
    grid = _grid()
    grid.schemi[0] = _schema("3-4-3", flexible)

    assert check_schemi(grid) == []


def test_a_slot_with_three_roles_is_accepted() -> None:
    """The published table prints one, so the gate must not refuse it.

    This test used to assert the opposite, on the reading that
    rules/sistema-mantra.md saying slots list "two roles" made two a ceiling. It
    does not. The first live collection returned `T/A/Pc` for 4-3-1-2 and was
    called "the collector exceeding its own brief"; the 2024-25 substitution
    table prints exactly that slot, on the pitch diagram and in the row label.
    The gate was written to reject the right answer, and the artefact L1 depends
    on was regenerated with the `T` dropped.
    """
    grid = _grid()
    grid.schemi[0] = _schema(
        "3-4-3",
        [
            ["DC"],
            ["DC"],
            ["DC"],
            ["DD", "E"],
            ["DS", "E"],
            ["C"],
            ["C"],
            ["T"],
            ["W"],
            ["T", "A", "PC"],
        ],
    )

    assert check_schemi(grid) == []


def test_a_slot_with_four_roles_is_still_rejected() -> None:
    """Three is what the source prints; four is nothing anyone has seen."""
    grid = _grid()
    grid.schemi[0] = _schema(
        "3-4-3",
        [
            ["DC"],
            ["DC"],
            ["DC"],
            ["DD", "E"],
            ["DS", "E"],
            ["C"],
            ["C"],
            ["T"],
            ["W"],
            ["T", "A", "PC", "C"],
        ],
    )

    assert any("3-4-3" in p for p in check_schemi(grid))


def test_the_matrix_must_say_where_it_came_from() -> None:
    # The compatibility table is a separate download this repo cannot diff against.
    # Recording the URLs actually read is the only way to tell a real collection
    # from the prompt's own example handed back.
    matrix = _matrix()
    matrix.fonti = []

    assert any("fonti" in p or "source" in p.lower() for p in check_compat(matrix, _grid()))


def test_an_unknown_role_code_is_rejected() -> None:
    grid = _grid()
    grid.schemi[2] = _schema(
        "3-5-2", [["DC"], ["DC"], ["DC"], ["DD"], ["ZZ"], ["M"], ["C"], ["T"], ["W"], ["PC"]]
    )

    problems = check_schemi(grid)

    assert any("ZZ" in p for p in problems)


def test_the_goalkeeper_is_not_one_of_the_ten_outfield_slots() -> None:
    # Por is fixed and outside the schema's four lines; finding it among the ten
    # means the collector mis-read the page.
    grid = _grid()
    grid.schemi[1] = _schema(
        "3-4-1-2", [["POR"], ["DC"], ["DC"], ["DD"], ["DS"], ["M"], ["C"], ["T"], ["W"], ["PC"]]
    )

    problems = check_schemi(grid)

    assert any("POR" in p for p in problems)


def test_duplicate_schema_names_are_rejected() -> None:
    assert check_schemi(_grid([*ELEVEN[:10], "4-3-3"])) != []


# --- compatibility matrix ------------------------------------------------


def test_a_well_formed_matrix_passes() -> None:
    assert check_compat(_matrix(), _grid()) == []


def test_a_matrix_missing_a_schema_is_rejected() -> None:
    problems = check_compat(_matrix(ELEVEN[:10]), _grid())

    assert any(ELEVEN[10] in p for p in problems)


def test_a_matrix_without_the_4_1_4_1_exception_is_rejected() -> None:
    # The one exception rules/sistema-mantra.md names explicitly: W and T are
    # normally interchangeable with a -1 malus, except in 4-1-4-1 where the swap
    # is impossible at any price. A matrix that lost it would silently permit an
    # illegal substitution every week.
    problems = check_compat(_matrix(exception=False), _grid())

    assert any("4-1-4-1" in p for p in problems)


def test_a_matrix_naming_a_schema_the_grid_does_not_have_is_rejected() -> None:
    assert check_compat(_matrix([*ELEVEN[:10], "6-3-1"]), _grid()) != []


def test_a_cell_that_is_not_one_of_the_four_values_is_rejected() -> None:
    matrix = _matrix()
    matrix.formazioni[0].slots[3].compat[5] = "forse"

    assert any("forse" in p for p in check_compat(matrix, _grid()))


def test_a_row_with_the_wrong_number_of_cells_is_rejected() -> None:
    matrix = _matrix()
    matrix.formazioni[0].slots[2].compat = ["ok", "no"]

    assert any("cells" in p for p in check_compat(matrix, _grid()))


def test_an_entry_with_no_rows_is_rejected() -> None:
    """The shape the old matrix had: a schema named, and nothing said about it.

    Ten of the eleven entries in the shipped file were exactly this — a name and
    an empty list — and every gate passed, because the only substantive check
    was the one fact the prompt had already supplied.
    """
    matrix = _matrix()
    matrix.formazioni[0].slots = []

    assert any("no rows" in p for p in check_compat(matrix, _grid()))


def test_a_slot_that_refuses_its_own_role_is_rejected() -> None:
    """The cheapest proof that a row was transcribed rather than guessed."""
    matrix = _matrix()
    matrix.formazioni[0].slots[1].compat[ROLE_ORDER.index("Dc")] = "-1"

    assert any("own role" in p for p in check_compat(matrix, _grid()))


def test_an_outfield_slot_that_accepts_a_keeper_is_rejected() -> None:
    matrix = _matrix()
    matrix.formazioni[0].slots[4].compat[ROLE_ORDER.index("Por")] = "-1"

    assert any("Por" in p for p in check_compat(matrix, _grid()))


def test_columns_out_of_order_are_rejected() -> None:
    """Every row is positional, so a reordered header silently rewrites the table."""
    matrix = _matrix()
    matrix.ruoli = [*ROLE_ORDER[1:], ROLE_ORDER[0]]

    assert any("order" in p for p in check_compat(matrix, _grid()))


def test_rows_that_do_not_match_the_grids_slots_are_rejected() -> None:
    matrix = _matrix()
    matrix.formazioni[0].slots[5] = _row("Pc")

    assert any("do not match" in p for p in check_compat(matrix, _grid()))


# --- purity --------------------------------------------------------------


def test_the_gates_touch_neither_the_sdk_nor_the_filesystem() -> None:
    from pathlib import Path as _Path

    source = (
        _Path(__file__).resolve().parent.parent / "src" / "fantabot" / "mantra_grid" / "gates.py"
    ).read_text()

    assert "claude_agent_sdk" not in source
    assert "open(" not in source
    assert "Path" not in source


def test_gates_report_every_problem_not_just_the_first() -> None:
    # A collector run is expensive; reporting one problem per attempt would make
    # fixing a bad transcription an N-round trip.
    grid = _grid(ELEVEN[:9])
    grid.schemi[0] = _schema("3-4-3", [["ZZ"]])

    assert len(check_schemi(grid)) >= 2


@pytest.mark.parametrize("checker", [check_schemi])
def test_gates_return_a_list_rather_than_raising(checker: object) -> None:
    assert isinstance(check_schemi(_grid(ELEVEN[:3])), list)


# --- the shipped artefacts ----------------------------------------------


def _load(name: str) -> dict[str, object]:
    """The shipped copy, resolved the way production resolves it."""
    from fantabot.resources import data_dir

    path = data_dir() / name
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_shipped_grid_and_matrix_pass_their_own_gates() -> None:
    """The gates were never run against what actually ships.

    `data/mantra_compat.json` held one entry and ten empty lists from
    2026-08-21 to 2026-08-28 and nothing noticed, because the gates only ever
    saw fixtures. A gate that never judges the artefact is a gate that judges
    nothing.
    """
    grid = SchemaGrid.model_validate(_load("mantra_schemi.json"))
    matrix = CompatMatrix.model_validate(_load("mantra_compat.json"))

    assert check_schemi(grid) == []
    assert check_compat(matrix, grid) == []


def test_the_shipped_matrix_is_the_whole_table() -> None:
    matrix = CompatMatrix.model_validate(_load("mantra_compat.json"))

    assert len(matrix.formazioni) == 11
    assert sum(len(f.slots) for f in matrix.formazioni) == 121, "11 slots per schema"
    cells = [c for f in matrix.formazioni for s in f.slots for c in s.compat]
    assert len(cells) == 1452


def test_the_shipped_matrix_keeps_the_distinction_l1_depends_on() -> None:
    """`-1*` is not `-1`.

    It means the platform refuses the placement at lineup submission and allows
    it only as the outcome of a forced substitution. Collapsing the two reads as
    "allowed with a malus" and produces lineups the site rejects. 120 cells say
    it, and the five prose rules in rules/sistema-mantra.md are exactly those
    cells: B/Dd/Ds into Dc, Dd against Ds, E into M, M into E, W into T.
    """
    matrix = CompatMatrix.model_validate(_load("mantra_compat.json"))
    cells = [c for f in matrix.formazioni for s in f.slots for c in s.compat]

    assert cells.count("-1*") == 120
    assert set(cells) == {"ok", "-1", "-1*", "no"}

    index = {code.upper(): i for i, code in enumerate(matrix.ruoli)}
    for slot_name, role in (("Dc", "Dd"), ("Dc", "Ds"), ("Dc", "B"), ("M", "E"), ("E", "M")):
        seen = [
            s.compat[index[role.upper()]]
            for f in matrix.formazioni
            for s in f.slots
            if s.slot.upper() == slot_name.upper()
        ]
        assert seen and set(seen) == {"-1*"}, (
            f"rules/sistema-mantra.md blocks {role} in a {slot_name} slot at "
            f"submission; the table should say -1* everywhere, got {set(seen)}"
        )


def test_the_shipped_matrix_blocks_the_4_1_4_1_swap_and_only_there() -> None:
    matrix = CompatMatrix.model_validate(_load("mantra_compat.json"))
    index = {code.upper(): i for i, code in enumerate(matrix.ruoli)}

    def swaps(entry: FormationCompat) -> set[str]:
        out = set()
        for row in entry.slots:
            codes = {p.strip().upper() for p in row.slot.split("/")}
            if "T" in codes and "W" not in codes:
                out.add(row.compat[index["W"]])
            if "W" in codes and "T" not in codes:
                out.add(row.compat[index["T"]])
        return out

    for entry in matrix.formazioni:
        values = swaps(entry)
        if entry.schema_nome == "4-1-4-1":
            assert values == {"no"}, "never, not even with a malus"
        else:
            assert "no" not in values, (
                f"{entry.schema_nome}: W and T are interchangeable with a malus "
                f"everywhere except 4-1-4-1, got {values}"
            )
