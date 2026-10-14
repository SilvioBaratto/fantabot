"""`plan_lineups` — the one place the lineup value model is composed. Pure, zero sockets.

Pins that the planner picks the best legal XI and bench from the value model, that sentiment
tilts the XI, and that `--no-sentiment` (effect=None) reproduces the fvm-only field.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from _paths import GOLDEN

from fantabot.application.lineup_planner import (
    LineupInputs,
    inputs_from_lineup,
    plan_lineups,
)

MODULES = ["3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"]

# 11 starters whose natural roles field only 3-4-3, each clearly above the reserves.
STARTERS: dict[int, tuple[float, tuple[str, ...]]] = {
    6482: (6.0, ("POR",)),
    2788: (8.0, ("DC",)),
    7564: (7.0, ("DC",)),
    7274: (6.0, ("DC",)),
    7181: (7.0, ("E",)),
    1850: (6.0, ("M",)),
    5504: (6.0, ("C",)),
    5678: (5.0, ("E",)),
    4179: (10.0, ("W",)),
    6875: (9.0, ("A",)),
    2194: (5.0, ("W",)),
}
RESERVE_GK = {50: (4.0, ("POR",))}
# broad-role reserves, all below the weakest starter, so the XI is exactly the starters
RESERVES = {60 + i: (4.0 - 0.2 * i, ("W", "A", "DC", "M", "C", "E")) for i in range(12)}

ALL = {**STARTERS, **RESERVE_GK, **RESERVES}
ROSTER_IDS = list(ALL)
ROLES = {pid: list(roles) for pid, (_, roles) in ALL.items()}
FVMMA = {pid: fvm for pid, (fvm, _) in ALL.items()}

INPUTS = LineupInputs(
    roster_ids=ROSTER_IDS,
    roles_by_id=ROLES,
    fvmma_by_id=FVMMA,
    modules=MODULES,
    competition=311681,
    mday=1,
    cmday=3,
    tid=10000003,
    bench_size=12,
)


def test_plans_the_best_legal_lineup_and_carries_the_matchday_coordinates() -> None:
    plan = plan_lineups(INPUTS)[0]

    assert plan.module == "343"
    assert set(plan.starts) == set(STARTERS)
    assert len(plan.starts) == 11
    assert len(plan.bench) == 12
    assert plan.bench[0] == 50  # reserve keeper
    assert (plan.competition, plan.mday, plan.cmday, plan.tid) == (311681, 1, 3, 10000003)


def test_the_field_is_the_top_value_players() -> None:
    plan = plan_lineups(INPUTS)[0]

    assert 2194 in plan.starts  # a starter (value 5.0) beats the reserves (< 5.0)
    assert 60 not in plan.starts  # a reserve (value 4.0) stays on the bench


def test_plan_lineups_are_ranked_best_first_each_complete() -> None:
    plans = plan_lineups(INPUTS)

    assert plans[0].module == "343"  # the roster fields only 343 here
    assert all(len(p.starts) == 11 and len(p.bench) == 12 for p in plans)


def test_inputs_from_lineup_maps_marle_roles_indexcompare_and_names() -> None:
    dto = {"mday": 1, "cmday": 3}  # note: no tid in the DTO
    info = [
        {"pid": 6482, "role": [6], "indexCompare": 5.5, "plyr": "Mandas"},
        {"pid": 7274, "role": [7, 9], "indexCompare": 6.7, "plyr": "Ze Pedro"},
    ]
    settings = {"mods": ["343", "442"], "tbench": 12}

    inputs, names = inputs_from_lineup(dto, info, settings, competition=311681, tid=10000003)

    assert list(inputs.roster_ids) == [6482, 7274]
    assert inputs.roles_by_id[7274] == ["Dd", "Dc"]  # marle 7,9
    assert inputs.fvmma_by_id[6482] == 5.5  # indexCompare is the value signal
    assert inputs.modules == ["343", "442"]
    assert (inputs.competition, inputs.mday, inputs.cmday, inputs.tid) == (311681, 1, 3, 10000003)
    assert inputs.bench_size == 12
    assert names[7274] == "Ze Pedro"


def test_tid_comes_from_the_argument_not_the_empty_dto() -> None:
    # first-of-season: the DTO is empty, the roster is still present via lineUpInfo
    info = [{"pid": 6482, "role": [6], "indexCompare": 5.5, "plyr": "Mandas"}]

    inputs, _ = inputs_from_lineup({}, info, {"mods": ["343"], "tbench": 12}, 311681, tid=999)

    assert inputs.tid == 999  # authoritative team id, never 0 from the empty DTO
    assert inputs.mday == 0 and inputs.cmday == 0  # missing coords surface as 0 (submit refuses)


def test_every_built_plan_passes_the_positional_guard() -> None:
    """The builder lays out natural roles in the platform's order, so the guard is silent."""
    assert all(plan.guard == "" for plan in plan_lineups(INPUTS))


def test_a_plan_the_guard_refuses_carries_the_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The guard is a second opinion on the builder, so it is injected here to disagree."""
    from fantabot.application import lineup_planner

    monkeypatch.setattr(
        lineup_planner.positional, "refusal", lambda module, _roles: "[6] C: M -1"
    )

    assert {plan.guard for plan in plan_lineups(INPUTS)} == {"[6] C: M -1"}


def test_a_classic_plan_is_not_judged_by_the_mantra_matrix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Classic's P/D/C/A are one role per player, and legality there is counting."""
    from fantabot.application import lineup_planner

    def boom(*_a: object) -> str:
        raise AssertionError("a Classic plan was judged by the Mantra matrix")

    monkeypatch.setattr(lineup_planner.positional, "refusal", boom)
    classic = LineupInputs(
        roster_ids=list(range(1, 26)),
        roles_by_id={
            i: ["P"] if i <= 3 else ["D"] if i <= 11 else ["C"] if i <= 19 else ["A"]
            for i in range(1, 26)
        },
        fvmma_by_id={i: float(i) for i in range(1, 26)},
        modules=["343", "442"],
        competition=1,
        mday=1,
        cmday=3,
        tid=1,
        bench_size=12,
        fmt="classic",
    )

    assert all(plan.guard == "" for plan in plan_lineups(classic))


# -- the platform's own "out": `lineUpInfo[i].status == 2` -------------------------------


def _golden_rows() -> list[dict[str, object]]:
    document = json.loads((GOLDEN / "lineup" / "teamLineup_read.json").read_text("utf-8"))
    return document["lineUpInfo"]  # type: ignore[no-any-return]


def _from(rows: list[dict[str, object]], *, fmt: str = "mantra") -> LineupInputs:
    inputs, _ = inputs_from_lineup(
        {"mday": 1, "cmday": 3}, rows, {"mods": ["343"], "tbench": 12}, 311681, tid=1, fmt=fmt
    )
    return inputs


class TestWhoThePlatformRulesOut:
    """`status == 2` on a `lineUpInfo` row, collected where the row is already being read."""

    def test_a_hand_built_input_rules_nobody_out(self) -> None:
        """Every caller that builds inputs by hand is unchanged."""
        assert INPUTS.ruled_out == frozenset()
        assert dict(INPUTS.ruled_out_notes) == {}

    def test_the_two_players_the_golden_fixture_marks_are_collected_by_id(self) -> None:
        """Rovella and Piotrowski, `status` 2 with `percent` 0, on 2026-09-22."""
        inputs, names = inputs_from_lineup(
            {"mday": 1, "cmday": 3}, _golden_rows(), {"mods": ["343"], "tbench": 12}, 311681,
            tid=1,
        )

        assert inputs.ruled_out == frozenset({4459, 7198})
        assert {names[pid] for pid in inputs.ruled_out} == {"Rovella", "Piotrowski"}

    def test_each_is_noted_by_name_with_the_platform_s_own_words(self) -> None:
        """The words come with the name because the note is read by a person at 16:00."""
        inputs = _from(_golden_rows())

        assert inputs.ruled_out_notes[4459] == (
            "Rovella (problema muscolare al polpaccio, rientro metà di ottobre)"
        )
        assert set(inputs.ruled_out_notes) == set(inputs.ruled_out)

    def test_a_player_whose_row_says_nothing_is_noted_by_name_alone(self) -> None:
        """The status decides; a missing reason costs the sentence and not the verdict."""
        inputs = _from([{"pid": 6482, "role": [6], "plyr": "Mandas", "status": 2}])

        assert inputs.ruled_out == frozenset({6482})
        assert inputs.ruled_out_notes[6482] == "Mandas"

    def test_a_player_with_no_name_is_noted_by_his_id(self) -> None:
        """`names` falls back to the id the same way."""
        inputs = _from([{"pid": 6482, "role": [6], "status": 2, "descr": "rientro a marzo"}])

        assert inputs.ruled_out_notes[6482] == "6482 (rientro a marzo)"

    def test_a_control_character_in_the_reason_does_not_reach_the_note(self) -> None:
        """The note is printed on a terminal and written to the JSONL record."""
        inputs = _from(
            [{"pid": 6482, "role": [6], "plyr": "Mandas", "status": 2, "descr": "a\x1b[2J\x00b"}]
        )

        note = inputs.ruled_out_notes[6482]
        assert note == "Mandas (a[2Jb)"
        assert note.isprintable()

    def test_status_one_everywhere_rules_nobody_out(self) -> None:
        """28 of the golden fixture's 30 rows are status 1; so is the whole fixture edited."""
        rows = _golden_rows()
        for row in rows:
            row["status"] = 1

        inputs = _from(rows)

        assert inputs.ruled_out == frozenset()
        assert dict(inputs.ruled_out_notes) == {}

    def test_changing_status_back_to_one_changes_nothing_else_in_the_inputs(self) -> None:
        """The flag is the only difference: roster, roles, values, pairings and module list
        are read as they were."""
        recorded = _from(_golden_rows())
        edited_rows = _golden_rows()
        for row in edited_rows:
            row["status"] = 1
        edited = _from(edited_rows)

        for field in (
            "roster_ids", "roles_by_id", "fvmma_by_id", "modules", "competition", "mday", "cmday",
            "tid", "bench_size", "fmt", "pairings",
        ):
            assert getattr(recorded, field) == getattr(edited, field), field

    @pytest.mark.parametrize(
        "status", [True, False, 2.0, "2", None, [2], {"status": 2}, 3, 0, -1, object()]
    )
    def test_any_other_status_costs_nothing(self, status: object) -> None:
        """Only the integer 2 is read; the parse never raises on the rest."""
        rows = _golden_rows()
        for row in rows:
            row["status"] = status

        assert _from(rows).ruled_out == frozenset()

    @pytest.mark.parametrize(
        "descr", [None, 7, ["x"], object(), "", pytest.param("é" * 10_000, id="very-long")]
    )
    def test_a_malformed_reason_costs_the_sentence_and_never_the_lineup(
        self, descr: object
    ) -> None:
        """A bad `descr` on every row still builds every input, and the note stays short."""
        rows = _golden_rows()
        for row in rows:
            row["status"] = 2
            row["descr"] = descr
            row["comment"] = descr

        inputs = _from(rows)

        assert list(inputs.roster_ids) == [row["pid"] for row in rows]
        assert inputs.ruled_out == frozenset(inputs.roster_ids)
        assert all(len(note) < 200 for note in inputs.ruled_out_notes.values())

    def test_a_classic_row_is_read_the_same_way(self) -> None:
        """The field is in both DTOs and the parse does not look at the format."""
        rows = [
            {"pid": 1, "fcrle": 1, "plyr": "Keeper", "status": 2, "descr": "rientro a marzo"},
            {"pid": 2, "fcrle": 2, "plyr": "Back", "status": 1, "percent": 0},
        ]

        inputs = _from(rows, fmt="classic")

        assert inputs.ruled_out == frozenset({1})

    def test_the_default_path_plans_the_same_xi_whoever_is_ruled_out(self) -> None:
        """`indexCompare` is the platform's own signal and is not touched: the flag is read
        only by the projection."""
        flagged = replace(
            INPUTS,
            ruled_out=frozenset({6482, 2788}),
            ruled_out_notes={6482: "Mandas", 2788: "Pellegrini"},
        )

        assert plan_lineups(flagged) == plan_lineups(INPUTS)

    def test_the_default_path_still_fields_a_status_two_man_read_through_the_real_parse(
        self,
    ) -> None:
        """The same claim, end to end: the golden roster with its best midfielder (`indexCompare`
        13.47) marked `status` 2. The parse flags him and leaves his value alone, and the
        `indexcompare` plan fields him exactly as it does at `status` 1 — the exclusion is the
        projection's, and this path is the platform's own number."""
        document = json.loads((GOLDEN / "lineup" / "teamLineup_read.json").read_text("utf-8"))
        settings = json.loads((GOLDEN / "lineup" / "lineup_settings.json").read_text("utf-8"))
        star = next(row for row in document["lineUpInfo"] if row["plyr"] == "Calhanoglu")
        control, _ = inputs_from_lineup(
            document["teamLineupDto"], document["lineUpInfo"], settings, 311681, tid=1
        )
        assert star["status"] == 1 and star["indexCompare"] == 13.47, "the setup"

        star.update(status=2, percent=0, descr="rientro a marzo")
        flagged, _ = inputs_from_lineup(
            document["teamLineupDto"], document["lineUpInfo"], settings, 311681, tid=1
        )

        assert 2194 in flagged.ruled_out
        assert flagged.fvmma_by_id[2194] == 13.47 == control.fvmma_by_id[2194]
        assert 2194 in plan_lineups(flagged)[0].starts
        assert plan_lineups(flagged) == plan_lineups(control)
