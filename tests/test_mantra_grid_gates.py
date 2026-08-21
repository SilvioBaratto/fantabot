"""T10: the gates that stand between a mis-transcribed grid and the whole season.

rules/sistema-mantra.md gives the general out-of-position rules in prose but says
outright that the full per-formation compatibility table "is a separate download
that isn't captured here". So the collector has to fetch something this repo
cannot check against a local copy — which is exactly when a gate earns its keep.

A gate with no failing fixture is not a gate, so every invariant here has one.
"""

import pytest

from fantabot.mantra_grid.gates import check_compat, check_schemi
from fantabot.mantra_grid.models import CompatMatrix, FormationCompat, MantraSchema, SchemaGrid

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


def _matrix(names: list[str] | None = None, exception: bool = True) -> CompatMatrix:
    return CompatMatrix(
        formazioni=[
            FormationCompat(
                schema_nome=n,
                vietati=[["W", "T"], ["T", "W"]] if (n == "4-1-4-1" and exception) else [],
            )
            for n in (names or ELEVEN)
        ],
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


def test_a_slot_with_three_roles_is_rejected() -> None:
    # rules/sistema-mantra.md: "Where a schema slot lists two roles, they're
    # interchangeable alternatives." Two, not three. The first live collection
    # returned T/A/Pc for 4-3-1-2, which is the collector exceeding its own brief —
    # and it slipped through because nothing constrained slot arity.
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

    problems = check_schemi(grid)

    assert any("3-4-3" in p and "2" in p for p in problems)


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


def test_an_unknown_role_in_a_blocked_pair_is_rejected() -> None:
    matrix = _matrix()
    matrix.formazioni[0].vietati = [["W", "ZZ"]]

    assert any("ZZ" in p for p in check_compat(matrix, _grid()))


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
