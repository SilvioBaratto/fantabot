"""`payload.build` — a `PlannedLineup` into the `gaming/v1/teamLineup` POST body. Pure.

Pinned field-for-field against the body captured live 2026-09-02 (`docs/leghe-api.md`): the
platform validates it strictly, so a missing or misnamed field is a `400`. `capt` stays
empty and `swtcMdl` mirrors `mdl` for this phase (SPEC open Qs 1-2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fantabot.domain.lineup.models import PlannedLineup
from fantabot.domain.lineup.payload import build

PLAN = PlannedLineup(
    module="343",
    starts=tuple(range(1, 12)),
    bench=tuple(range(20, 32)),
    competition=311681,
    mday=1,
    cmday=3,
    tid=10000003,
)


def test_the_body_has_exactly_the_captured_fields() -> None:
    body = build(PLAN)

    assert set(body) == {
        "starts", "bench", "capt", "mdl", "idcomp", "mday", "cmday", "tid",
        "allComp", "visb", "swtcA", "swtcB", "swtc", "swtcMdl",
    }


def test_the_lineup_fields_pass_through() -> None:
    body = build(PLAN)

    assert body["starts"] == list(range(1, 12)) and len(body["starts"]) == 11
    assert body["bench"] == list(range(20, 32)) and len(body["bench"]) == 12
    assert body["mdl"] == "343"
    assert (body["idcomp"], body["mday"], body["cmday"], body["tid"]) == (311681, 1, 3, 10000003)


def test_the_phase_constants_match_the_captured_body() -> None:
    body = build(PLAN)

    assert body["capt"] == []
    assert body["swtcMdl"] == "343"  # mirrors mdl
    assert body["swtcA"] == body["swtcB"] == body["swtc"] == 0
    assert body["allComp"] is False
    assert body["visb"] is True


def _captured(name: str) -> dict[str, object]:
    path = Path(__file__).parents[2] / "fixtures" / "lineup" / name
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def _plan_from(captured: dict[str, Any], *, switch_module: str | None) -> PlannedLineup:
    return PlannedLineup(
        module=captured["mdl"],
        starts=tuple(captured["starts"]),
        bench=tuple(captured["bench"]),
        competition=captured["idcomp"],
        mday=captured["mday"],
        cmday=captured["cmday"],
        tid=captured["tid"],
        all_comp=captured["allComp"],
        captains=tuple(captured["capt"]),
        switch=(captured["swtcA"], captured["swtcB"]),
        switch_module=switch_module,
    )


def test_a_cross_role_switch_matches_the_body_captured_with_one_set() -> None:
    # Captured live 2026-09-23, lega 3677376: Delprato (D, 6664) out, Chukwueze (C, 4856) in —
    # 433 becomes 343, and `swtcMdl` names the module after the swap.
    captured: dict[str, Any] = _captured("submit_cross_role_switch.json")

    body = build(_plan_from(captured, switch_module="343"))

    assert body == {k: v for k, v in captured.items() if k != "pos"}


def test_captain_and_switch_match_the_body_captured_with_both_set() -> None:
    # Captured live 2026-09-23, lega 3677376: captain + vice and a switch set by hand. `pos`
    # was in that body too and is not sent: every earlier submit saved without it.
    captured: dict[str, Any] = _captured("submit_capt_switch.json")
    plan = PlannedLineup(
        module=captured["mdl"],
        starts=tuple(captured["starts"]),
        bench=tuple(captured["bench"]),
        competition=captured["idcomp"],
        mday=captured["mday"],
        cmday=captured["cmday"],
        tid=captured["tid"],
        all_comp=captured["allComp"],
        captains=tuple(captured["capt"]),
        switch=(captured["swtcA"], captured["swtcB"]),
    )

    assert build(plan) == {k: v for k, v in captured.items() if k != "pos"}
