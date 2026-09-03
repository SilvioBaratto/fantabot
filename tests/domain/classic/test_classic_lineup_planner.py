"""Classic weekly lineup from a teamLineup response: roles come from fcrle, XI + bench build."""

from __future__ import annotations

from fantabot.application.lineup_planner import inputs_from_lineup, plan_lineups


def _row(pid: int, fcrle: int, val: float) -> dict[str, object]:
    return {"pid": pid, "fcrle": fcrle, "indexCompare": val, "plyr": f"P{pid}"}


def _classic_roster() -> list[dict[str, object]]:
    # a full Classic rosa: 3 P, 9 D, 9 C, 6 A (fcrle 1/2/3/4), values descending within a role.
    rows: list[dict[str, object]] = []
    pid = 1
    for fcrle, n in ((1, 3), (2, 9), (3, 9), (4, 6)):
        for i in range(n):
            rows.append(_row(pid, fcrle, float(n - i)))
            pid += 1
    return rows


def test_a_classic_roster_fields_an_xi_and_a_bench_from_fcrle() -> None:
    inputs, names = inputs_from_lineup(
        dto={"mday": 1, "cmday": 1},
        lineup_info=_classic_roster(),
        settings={"mods": ["352", "442"], "tbench": 9},
        competition=7,
        tid=17,
        fmt="classic",
    )
    assert inputs.fmt == "classic"
    plans = plan_lineups(inputs)
    best = plans[0]
    assert best.module in ("352", "442")
    assert len(best.starts) == 11
    assert len(best.bench) == 9
    # bench slot 0 is a reserve keeper (fcrle 1 -> role P); ids 1..3 are the keepers.
    assert best.bench[0] in (1, 2, 3)
    assert names[1] == "P1"


def test_mantra_stays_the_default_source() -> None:
    # a Mantra row uses `role` marle codes, not fcrle; the default fmt must read those.
    rows = [{"pid": 1, "role": [6], "indexCompare": 5.0, "plyr": "K"}]
    inputs, _ = inputs_from_lineup(
        dto={"mday": 1, "cmday": 1}, lineup_info=rows, settings={"mods": [], "tbench": 12},
        competition=7, tid=17,
    )
    assert inputs.fmt == "mantra"
    assert inputs.roles_by_id[1]  # marle 6 resolved to a Mantra role, not read as fcrle
