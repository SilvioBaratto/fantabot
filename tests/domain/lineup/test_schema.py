"""`schema.slots` — a module code to its ordered slot role-sets, GK first.

The platform's `starts[]` is 11 ids in slot order with the goalkeeper first, and it judges
`starts[i]` against **its own** slot `i`. That order is not `mantra_schemi.json`'s: the
schemi list each schema's slots in the PDF table's row order, and sending `starts[]` in that
order is what drew `LUP009` on 7 of 11 modules (and, where a swapped cell is `-1` rather
than a refusal, a malus nobody would see). The order now comes from
`mantra_starts_order.json`, read from the platform's own bundle (SPEC A22,
`docs/lineup-slot-order.md`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from fantabot.domain.asta.legality import load_compat
from fantabot.domain.lineup import positional, schema
from fantabot.domain.lineup.build import lineup_for_module
from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.shared.resources import SCHEMI_FILENAME, data_dir

#: The platform's own per-module order, verbatim from `S.schemes.mantra` in
#: leghe.fantacalcio.it's `resources/chunk-Dc2l8Fqx.js` (read 2026-09-21, sha256 421ca595…).
#: A literal, not a read of the shipped file: a pin that loads what it judges pins nothing.
PLATFORM_ORDER = {
    "343": "Por Dc/B Dc Dc E C M/C E W/A A/Pc W/A",
    "3412": "Por Dc/B Dc Dc E C M/C E T A/Pc A/Pc",
    "3421": "Por Dc/B Dc Dc E M/C M E/W T/A T A/Pc",
    "352": "Por Dc/B Dc Dc E C M M/C E/W A/Pc A/Pc",
    "442": "Por Ds Dc Dc Dd E C M/C E/W A/Pc A/Pc",
    "433": "Por Ds Dc Dc Dd C M M/C W/A A/Pc W/A",
    "4312": "Por Ds Dc Dc Dd C M M/C T A/Pc T/A/Pc",
    "3511": "Por Dc/B Dc Dc E/W M C M E/W T/A A/Pc",
    "4141": "Por Ds Dc Dc Dd M W T C/T E/W A/Pc",
    "4411": "Por Ds Dc Dc Dd E/W C M E/W T/A A/Pc",
    "4231": "Por Ds Dc Dc Dd M/C M W/A T T/W A/Pc",
}


def _role_sets(labels: str) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(role.upper() for role in label.split("/")) for label in labels.split())


def _schemi_row_order(module_code: str) -> tuple[frozenset[str], ...]:
    """GK + `mantra_schemi.json`'s slots — the order the builder sent before the fix."""
    raw = json.loads((data_dir() / SCHEMI_FILENAME).read_text(encoding="utf-8"))
    for entry in raw["schemi"]:
        if entry["nome"].replace("-", "") == module_code:
            outfield = (frozenset(role.upper() for role in slot) for slot in entry["slots"])
            return (frozenset({"POR"}), *outfield)
    raise ValueError(module_code)


def test_the_343_slots_are_the_platforms_order_not_the_pdf_rows() -> None:
    """Changed on purpose — the Criterion 7 exception SPEC A18 names.

    This test pinned the PDF row order (`Dc Dc Dc/B … M/C C …`) until 2026-09-21. The
    platform reads position 1 as `Dc/B` and position 5 as `C`, so that order put an M-only
    player in a pure-C slot: accepted, with a -1 nobody would have seen.
    """
    assert schema.slots("343") == (
        frozenset({"POR"}),
        frozenset({"DC", "B"}),
        frozenset({"DC"}),
        frozenset({"DC"}),
        frozenset({"E"}),
        frozenset({"C"}),
        frozenset({"M", "C"}),
        frozenset({"E"}),
        frozenset({"W", "A"}),
        frozenset({"A", "PC"}),
        frozenset({"W", "A"}),
    )


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_every_module_follows_the_platforms_positional_order(code: str) -> None:
    assert schema.slots(code) == _role_sets(PLATFORM_ORDER[code])


@pytest.mark.parametrize("code", ["343", "3412", "3421", "352", "3511"])
def test_a_three_back_puts_dc_or_b_at_position_one(code: str) -> None:
    """SPEC A22: `Dc/B`, as the page validates it — not the lineup assistant's `Dc`.

    The chunk holds a second table whose position 1 is plain `Dc`. If the server judged by
    that one, a B-only player here would be refused (LUP009), never given a silent -1.
    """
    assert schema.slots(code)[1] == frozenset({"DC", "B"})


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_the_order_is_a_permutation_of_the_schemas_slots(code: str) -> None:
    """Same slots, different positions: the fix may move a slot, never change one."""
    assert sorted(schema.slots(code), key=sorted) == sorted(_schemi_row_order(code), key=sorted)


def _deep_roster() -> list[RosterPlayer]:
    """Three single-role players per Mantra role, every value distinct."""
    roles = ["POR", "DD", "DS", "DC", "B", "E", "M", "C", "T", "W", "A", "PC"]
    return [
        RosterPlayer(
            id=100 * r + k, roles=frozenset({role}), fvmma=float((37 * (100 * r + k)) % 101)
        )
        for r, role in enumerate(roles, start=1)
        for k in range(3)
    ]


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_a_modules_best_total_is_unchanged_by_the_reorder(code: str) -> None:
    """The matching's value is permutation-invariant; only where each player is sent moves."""
    roster = _deep_roster()
    value = {p.id: p.fvmma for p in roster}

    new = lineup_for_module(roster, code, value=value)
    old = lineup_for_module(roster, code, value=value, slots_provider=_schemi_row_order)

    assert new is not None and old is not None
    assert sum(value[pid] for pid in new) == sum(value[pid] for pid in old)


def test_every_slot_list_has_eleven_slots() -> None:
    for code in schema.modules():
        assert len(schema.slots(code)) == 11, code


def test_all_eleven_platform_modules_resolve() -> None:
    expected = {"3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"}

    assert schema.modules() == expected


def test_an_unknown_module_code_raises() -> None:
    with pytest.raises(ValueError, match="999"):
        schema.slots("999")


# -- T20: the compat matrix, re-laid into the platform's slot order -------------------


def test_the_two_files_describe_the_same_eleven_schemi() -> None:
    """`mantra_compat.json` and `mantra_starts_order.json` are two views of one thing — the
    PDF's row order and the platform's `starts[]` order. `admissions` matches them slot by
    slot, so they must agree on which slots exist before the match means anything."""
    from fantabot.domain.asta.legality import build_legality

    compat = {nome.replace("-", "") for nome in build_legality(load_compat())}

    assert compat == schema.modules()


def test_every_slot_is_matched_by_role_set_and_not_by_label() -> None:
    """4-2-3-1 spells one slot `T/W` in the starts order and `W/T` in the compat matrix. A
    `str` key drops that slot on exactly one of the eleven, and the one it drops is the
    module whose `T` slot carries the `-1*` `W` cell — so the failure is silent and lands
    on the rule the 4-1-4-1 exception is a *counter*example to."""
    labels = {
        code: [sorted(slot.natural) for slot in schema.admissions(code)]
        for code in sorted(schema.modules())
    }

    assert labels["4231"][9] == ["T", "W"]
    assert "W" in schema.admissions("4231")[9].natural


@pytest.mark.parametrize("code", sorted({"3412", "3421", "343", "3511", "352", "4141",
                                         "4231", "4312", "433", "4411", "442"}))
def test_the_natural_roles_of_an_admission_are_the_slot_itself(code: str) -> None:
    """`admissions(code)[i].natural` must be `slots(code)[i]` exactly. Anything looser and
    the malus count is measured against a different schema from the one being fielded."""
    assert tuple(slot.natural for slot in schema.admissions(code)) == schema.slots(code)


@pytest.mark.parametrize("code", sorted({"3412", "3421", "343", "3511", "352", "4141",
                                         "4231", "4312", "433", "4411", "442"}))
def test_each_module_admits_eleven_slots(code: str) -> None:
    assert len(schema.admissions(code)) == 11


def test_an_unknown_module_has_no_admissions_rather_than_an_empty_one() -> None:
    with pytest.raises(ValueError, match="999"):
        schema.admissions("999")


# -- T5: the two readings of `mantra_compat.json`, collapsed into one ------------------


def _shipped_repeats() -> list[tuple[str, str]]:
    """Every `(schema, label)` whose role set another row of the same schema also has."""
    matrix = load_compat()
    out: list[tuple[str, str]] = []
    for entry in matrix.formazioni:
        seen: set[frozenset[str]] = set()
        for row in entry.slots:
            key = frozenset(part.strip().upper() for part in row.slot.split("/"))
            if key in seen:
                out.append((entry.schema_nome, row.slot))
            seen.add(key)
    return out


def test_the_shipped_matrix_really_does_repeat_slot_role_sets() -> None:
    """Without this the refusal below would be guarding a case that cannot arise.

    Repeats are the norm, not an edge: three `Dc` rows in every three-back, two `A/Pc` in
    4-4-2. 21 of the 121 rows, measured 2026-09-24 — and every one of them agrees, which
    is a property of a transcription from a PDF and not one anybody chose.
    """
    assert len(_shipped_repeats()) == 21


@pytest.fixture
def disagreeing_matrix(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """3-4-3's second `Dc` row, with one cell moved. Both readings must refuse it."""
    matrix = load_compat().model_copy(deep=True)
    entry = next(f for f in matrix.formazioni if f.schema_nome == "3-4-3")
    rows = [i for i, s in enumerate(entry.slots) if s.slot.strip().upper() == "DC"]
    entry.slots[rows[1]].compat[list(matrix.ruoli).index("W")] = "ok"

    monkeypatch.setattr(schema, "load_compat", lambda source=None: matrix)
    monkeypatch.setattr(positional, "load_compat", lambda source=None: matrix)
    schema._admissions_by_code.cache_clear()
    positional._rows.cache_clear()
    try:
        yield
    finally:
        schema._admissions_by_code.cache_clear()
        positional._rows.cache_clear()


def test_both_readings_refuse_a_schema_whose_repeated_slots_disagree(
    disagreeing_matrix: None,
) -> None:
    """They answered this differently until 2026-09-24, and the difference was silent.

    `positional._rows` raised; `_admissions_by_code` tolerated it, taking the repeats
    positionally (`setdefault(...).append` then `pop(0)`) — so the slot at the platform's
    position *i* got whichever row happened to sit at the PDF's position *i*. Measured on
    this exact matrix: `positional` raised while `admissions("343")` handed the two `Dc`
    slots different rules, one admitting a `W` and one not. One of those readings guards a
    submission and the other picks a substitute; they were never equally safe.
    """
    xi = [frozenset({"POR"})] * 11
    with pytest.raises(ValueError, match="two 'Dc' rows disagree"):
        positional.cells("343", xi)
    with pytest.raises(ValueError, match="two 'Dc' rows disagree"):
        schema.admissions("343")


@pytest.mark.parametrize("code", sorted(PLATFORM_ORDER))
def test_the_cells_and_the_admissions_describe_the_same_schema(code: str) -> None:
    """The cross-check that made collapsing them safe, kept as the guard against a split.

    `positional` reads the raw cells because it must tell `ok` from `-1`; `admissions`
    reads them folded into sets. Same rows, same pairing, so `submission` is exactly the
    `ok`/`-1` cells and `substitution` those plus `-1*`, at all 11 positions of all 11
    modules.
    """
    roles = [role.upper() for role in load_compat().ruoli]
    for position, (slot, admission) in enumerate(
        zip(schema.slots(code), schema.admissions(code), strict=True)
    ):
        cells = positional.cells(code, [slot] * 11)[position]
        assert cells.slot == slot
        row = {
            role: positional.cells(code, [frozenset({role})] * 11)[position].value
            for role in roles
        }
        assert admission.submission == frozenset(
            r for r, v in row.items() if v in {"ok", "-1"}
        ), f"{code}[{position}]"
        assert admission.substitution == frozenset(
            r for r, v in row.items() if v in {"ok", "-1", "-1*"}
        ), f"{code}[{position}]"
