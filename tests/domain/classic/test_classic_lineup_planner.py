"""Classic weekly lineup from a teamLineup response: roles come from fcrle, XI + bench build."""

from __future__ import annotations

from fantabot.application.lineup_planner import inputs_from_lineup, plan_lineup, plan_lineups
from fantabot.domain.lineup.payload import build


def _row(pid: int, fcrle: int, val: float) -> dict[str, object]:
    return {"pid": pid, "fcrle": fcrle, "indexCompare": val, "plyr": f"P{pid}"}


def _distinct_roster() -> list[dict[str, object]]:
    # pids 1-3 P, 4-12 D, 13-21 C, 22-27 A; value = 100 - pid, so all distinct (no tie to
    # make the pinned XI/bench ambiguous). Lower pid = higher value.
    rows: list[dict[str, object]] = []
    pid = 1
    for fcrle, n in ((1, 3), (2, 9), (3, 9), (4, 6)):
        for _ in range(n):
            rows.append(_row(pid, fcrle, 100.0 - pid))
            pid += 1
    return rows


def _classic_inputs(rows, *, mods, tbench=9):  # type: ignore[no-untyped-def]
    inputs, names = inputs_from_lineup(
        dto={"mday": 5, "cmday": 7}, lineup_info=rows,
        settings={"mods": mods, "tbench": tbench}, competition=311, tid=99, fmt="classic",
    )
    return inputs, names


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


def test_the_classic_plan_is_deterministic() -> None:
    # a golden-style pin: fixed distinct values -> exactly one best 352 XI and bench order.
    inputs, _ = _classic_inputs(_distinct_roster(), mods=["352"])
    plan = plan_lineup(inputs)

    assert plan.module == "352"
    # GK first, then the top 3 D, top 5 C, top 2 A by value (lower pid = higher value).
    assert plan.starts == (1, 4, 5, 6, 13, 14, 15, 16, 17, 22, 23)
    assert plan.bench[0] == 2  # the highest-value reserve keeper
    assert len(plan.bench) == 9
    assert (plan.mday, plan.cmday, plan.tid, plan.competition) == (5, 7, 99, 311)


def test_the_classic_payload_carries_the_formation_code() -> None:
    inputs, _ = _classic_inputs(_distinct_roster(), mods=["352"])
    body = build(plan_lineup(inputs))

    assert body["mdl"] == "352"
    assert body["swtcMdl"] == "352"
    assert len(body["starts"]) == 11
    assert body["tid"] == 99
    assert body["idcomp"] == 311
