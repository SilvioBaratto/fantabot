"""L1 — Mantra roster legality (bipartite schema matching). Pure and synchronous.

A rosa "fields" a schema when its players admit a perfect matching onto the schema's
11 slots (the fixed Por + the 10 movement slots), each player placed in a slot its role
is allowed in. "Allowed at lineup submission" means the compat cell is `ok` or `-1`;
`-1*` is refused at submission and admitted only as the outcome of a forced substitution;
`no` is never admitted. The load-bearing distinction is `-1*` vs `-1` — collapsing it
builds lineups the platform rejects.
"""

from __future__ import annotations

import pytest

from fantabot.asta_engine.legality import (
    SchemaLegality,
    SlotRule,
    build_legality,
    can_field,
    fieldable_schemi,
    load_compat,
    marginal_legality,
    slot_allows,
)
from fantabot.asta_engine.roles import MANTRA_ROLES, MantraPlayer, normalize_role, normalize_roles

# Built once from the shipped, verified artefact — the real 1,452-cell matrix.
LEGALITY = build_legality(load_compat())


def _player(pid: str, *roles: str) -> MantraPlayer:
    return MantraPlayer(id=pid, roles=normalize_roles(roles))


# --- roles ------------------------------------------------------------------------------


def test_twelve_canonical_roles_uppercase() -> None:
    assert frozenset(
        {"POR", "DD", "DS", "DC", "B", "E", "M", "C", "T", "W", "A", "PC"}
    ) == MANTRA_ROLES
    assert normalize_role("Dc") == "DC"  # JSON mixed-case → canonical upper
    assert normalize_role("por") == "POR"


def test_an_unknown_role_is_rejected_not_guessed() -> None:
    with pytest.raises(ValueError):
        normalize_role("Trequartista")


# --- the matrix loads whole -------------------------------------------------------------


def test_all_eleven_schemi_with_eleven_slots_each() -> None:
    assert len(LEGALITY) == 11
    for schema in LEGALITY.values():
        assert len(schema.slots) == 11  # Por + 10 movement


# --- the -1* semantics (the whole reason the matrix stores it) --------------------------


def test_minus_one_star_refused_at_submission_allowed_in_substitution() -> None:
    seen = 0
    for schema in LEGALITY.values():
        for slot in schema.slots:
            for role in slot.substitution - slot.submission:  # exactly the -1* roles
                seen += 1
                assert not slot_allows(role, slot, "submission")
                assert slot_allows(role, slot, "substitution")
    assert seen >= 120, f"expected the matrix's 120 -1* cells, saw {seen}"


def test_the_five_prose_rules_are_never_submission_placements() -> None:
    # rules/sistema-mantra.md: these out-of-position moves are -1* (or, in 4-1-4-1,
    # outright `no`) — never placeable when the lineup is built, only after a forced
    # substitution. The rule is "intruder refused at submission in that slot".
    rules: dict[str, tuple[str, ...]] = {
        "Dc": ("B", "DD", "DS"),  # B/Dd/Ds into Dc
        "Dd": ("B", "DS"),        # Dd against Ds
        "Ds": ("B", "DD"),
        "E": ("M",),              # E into M / M into E
        "M": ("E",),
        "T": ("W",),              # W into T
    }
    checked = 0
    for schema in LEGALITY.values():
        for slot in schema.slots:
            for intruder in rules.get(slot.name, ()):
                checked += 1
                assert not slot_allows(intruder, slot, "submission"), (
                    f"{intruder} must be refused at submission in a {slot.name} slot ({schema.nome})"
                )
    assert checked > 0


def test_w_into_t_is_admitted_after_a_substitution_where_the_swap_is_allowed() -> None:
    # Most schemi admit W→T as -1*; 4-1-4-1 forbids the swap outright (`no`). So at
    # least one T slot admits W in substitution, and none admit it at submission.
    t_slots = [s for sch in LEGALITY.values() for s in sch.slots if s.name == "T"]
    assert t_slots
    assert all(not slot_allows("W", s, "submission") for s in t_slots)
    assert any(slot_allows("W", s, "substitution") for s in t_slots)


# --- fieldability (the bipartite matcher) -----------------------------------------------


def test_a_native_rosa_fields_its_schema() -> None:
    schema = LEGALITY["3-4-3"]
    rosa = [_player(f"p{i}", sorted(slot.submission)[0]) for i, slot in enumerate(schema.slots)]
    assert can_field(rosa, schema, "submission")
    assert "3-4-3" in fieldable_schemi(rosa, LEGALITY)


def test_a_rosa_missing_a_goalkeeper_cannot_field() -> None:
    schema = LEGALITY["3-4-3"]
    # Fill only the 10 movement slots; no Por player → the Por slot cannot be matched.
    movement = [s for s in schema.slots if s.name != "Por"]
    rosa = [_player(f"p{i}", sorted(slot.submission)[0]) for i, slot in enumerate(movement)]
    assert not can_field(rosa, schema, "submission")


def test_marginal_legality_of_adding_a_goalkeeper() -> None:
    schema = LEGALITY["3-4-3"]
    movement = [s for s in schema.slots if s.name != "Por"]
    rosa = [_player(f"p{i}", sorted(slot.submission)[0]) for i, slot in enumerate(movement)]
    gained = marginal_legality(rosa, _player("gk", "POR"), LEGALITY)
    assert "3-4-3" in gained  # the keeper unlocks the schema the rosa could not field


# --- the matcher itself, on a hand-built schema (mode + perfect matching) ---------------


def _schema(*slots: SlotRule) -> SchemaLegality:
    return SchemaLegality(nome="test", slots=tuple(slots))


def test_matcher_requires_a_distinct_player_per_slot() -> None:
    two = _schema(
        SlotRule(name="A", submission=frozenset({"A"}), substitution=frozenset({"A"})),
        SlotRule(name="A2", submission=frozenset({"A"}), substitution=frozenset({"A"})),
    )
    assert not can_field([_player("only", "A")], two, "submission")  # one player, two slots
    assert can_field([_player("x", "A"), _player("y", "A")], two, "submission")


def test_matcher_honours_the_mode_on_a_star_only_slot() -> None:
    star = _schema(
        SlotRule(name="T", submission=frozenset(), substitution=frozenset({"W"})),
    )
    assert not can_field([_player("w", "W")], star, "submission")
    assert can_field([_player("w", "W")], star, "substitution")


# --- the boundary: L1 is pure ------------------------------------------------------------


def test_pure_modules_reach_no_database_or_network() -> None:
    import ast
    import pathlib

    pkg = pathlib.Path(__file__).resolve().parents[1] / "src" / "fantabot" / "asta_engine"
    forbidden = ("fantabot.db", "playwright", "claude_agent_sdk", "socket", "httpx", "sqlalchemy")
    for name in ("roles.py", "legality.py"):
        tree = ast.parse((pkg / name).read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        bad = [m for m in modules for f in forbidden if m == f or m.startswith(f + ".")]
        assert not bad, f"{name} reaches I/O: {bad}"
