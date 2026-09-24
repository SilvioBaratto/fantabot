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


def test_a_live_classic_row_without_fcrle_reads_its_role_list() -> None:
    # the live Classic lineUpInfo (2026-09-22) has no `fcrle`; the macro role is `role: [n]`.
    rows = [
        {"pid": 1, "role": [1], "indexCompare": 4.3, "plyr": "Butez"},
        {"pid": 2, "role": [4], "indexCompare": 17.4, "plyr": "Malen"},
    ]
    inputs, _ = inputs_from_lineup(
        dto={"mday": 3, "cmday": 6}, lineup_info=rows, settings={"mods": [], "tbench": 12},
        competition=7, tid=17, fmt="classic",
    )
    assert inputs.roles_by_id == {1: ["P"], 2: ["A"]}


def _back_three_roster() -> list[dict[str, object]]:
    # 3 P, 5 D (weak), 9 C, 8 A (strong): on raw sums a 3-4-3 beats every 4-back module.
    rows: list[dict[str, object]] = []
    pid = 1
    for fcrle, n, val in ((1, 3, 5.0), (2, 5, 1.0), (3, 9, 6.0), (4, 8, 9.0)):
        for i in range(n):
            rows.append(_row(pid, fcrle, val - 0.01 * i))
            pid += 1
    return rows


def test_the_defence_modifier_puts_a_four_back_module_first() -> None:
    inputs, _ = _classic_inputs(_back_three_roster(), mods=["343", "442", "433"])

    plans = plan_lineups(inputs)

    assert inputs.defence_modifier
    assert plans[0].module in ("433", "442")
    # the sum winner is kept, but only as a fallback after every 4-back module
    assert [p.module for p in plans][-1] == "343"


def test_without_the_modifier_the_best_sum_wins() -> None:
    import dataclasses

    inputs, _ = _classic_inputs(_back_three_roster(), mods=["343", "442", "433"])

    plans = plan_lineups(dataclasses.replace(inputs, defence_modifier=False))

    assert plans[0].module == "343"


def test_a_back_three_is_still_fielded_when_no_four_back_module_is_allowed() -> None:
    inputs, _ = _classic_inputs(_back_three_roster(), mods=["343", "352"])

    assert plan_lineup(inputs).module in ("343", "352")


def _rules(league: int):  # type: ignore[no-untyped-def]
    """The lega's real `settings/calculate` body, captured 2026-09-23, parsed."""
    import json
    from pathlib import Path

    from fantabot.domain.lineup.rules import rules_from_calculate

    path = Path(__file__).parents[2] / "fixtures" / "lineup" / f"calculate_{league}.json"
    return rules_from_calculate(json.loads(path.read_text()))


def _four_back_inputs(
    *, doubtful_p: float, cross_role: bool, mods: list[str], league: int = 2761635
):  # type: ignore[no-untyped-def]
    """3 P, exactly 4 D (the 4th with `doubtful_p`), 9 C, 8 A, on realistic scores: swapping an
    attacker for a defender costs ~1 point, which a likely bonus (~2.5 at a 6.6 vote average)
    outweighs and an unlikely one does not. No defender on the bench."""
    import dataclasses

    from fantabot.application.lineup_planner import with_rules
    from fantabot.domain.lineup.predict import Prediction

    rows: list[dict[str, object]] = []
    p_play: dict[int, float] = {}
    pid = 1
    for fcrle, n, val in ((1, 3, 5.0), (2, 4, 5.5), (3, 9, 6.0), (4, 8, 6.5)):
        for i in range(n):
            rows.append(_row(pid, fcrle, val - 0.01 * i))
            p_play[pid] = doubtful_p if (fcrle == 2 and i == 3) else 0.95
            pid += 1
    inputs, _ = _classic_inputs(rows, mods=mods)
    preds = {}
    for r in rows:
        rid, val = int(str(r["pid"])), float(str(r["indexCompare"]))
        preds[rid] = Prediction(
            pid=rid, p_play=p_play[rid], fv_if_plays=val, expected=p_play[rid] * val,
            score=val, factors={}, vote_if_plays=6.6,
        )
    inputs = dataclasses.replace(
        inputs, predictions=preds, switch_enabled=True, switch_cross_role=cross_role
    )
    return with_rules(inputs, _rules(league))


def test_a_likely_bonus_puts_the_back_four_first() -> None:
    plans = plan_lineups(
        _four_back_inputs(doubtful_p=0.95, cross_role=False, mods=["343", "442"])
    )

    best = plans[0]
    assert best.module == "442"
    assert best.defence_bonus_p is not None and best.defence_bonus_p > 0.75
    assert best.defence_bonus_ev is not None and best.defence_bonus_ev > 1.0
    assert best.max_subs == 5  # the lega's own cap


def test_an_unlikely_bonus_is_not_worth_the_points() -> None:
    plans = plan_lineups(
        _four_back_inputs(doubtful_p=0.2, cross_role=False, mods=["343", "442"])
    )

    assert plans[0].module == "343"  # P * E[bonus] is below the ~1 point the D costs


def test_a_cross_role_switch_makes_the_risky_back_four_worth_it() -> None:
    plans = plan_lineups(
        _four_back_inputs(
            doubtful_p=0.2, cross_role=True, mods=["343", "442", "352"], league=3677376
        )
    )

    best = plans[0]
    assert best.module == "442"
    assert best.switch is not None
    assert best.switch[0] == 7  # the doubtful 4th defender (pids 4-7 are the D)
    assert 8 <= best.switch[1] <= 16  # a bench midfielder (pids 8-16 are the C)
    assert best.switch_module == "352"


def test_a_lega_without_the_modifier_does_not_chase_it() -> None:
    import dataclasses

    from fantabot.application.lineup_planner import with_rules
    from fantabot.domain.lineup.rules import LeagueRules

    inputs = _four_back_inputs(doubtful_p=0.95, cross_role=False, mods=["343", "442"])
    inputs = with_rules(
        dataclasses.replace(inputs), LeagueRules(defence=None, captain=None, subs=None)
    )

    assert not inputs.defence_modifier
    assert plan_lineups(inputs)[0].module == "343"


def test_a_zero_tbench_follows_the_saved_bench_size() -> None:
    # lega 4219373: tbench 0, and the platform's saved bench held 13 of 14 reserves.
    rows = _classic_roster()
    inputs, _ = inputs_from_lineup(
        dto={"mday": 3, "cmday": 6, "bench": list(range(100, 113))}, lineup_info=rows,
        settings={"mods": ["352"], "tbench": 0}, competition=7, tid=17, fmt="classic",
    )

    assert inputs.bench_size == 13
    assert len(plan_lineup(inputs).bench) == 13


def test_a_zero_tbench_with_nothing_saved_benches_every_reserve() -> None:
    rows = _classic_roster()  # 27 players -> 16 reserves
    inputs, _ = inputs_from_lineup(
        dto={"mday": 3, "cmday": 6}, lineup_info=rows,
        settings={"mods": ["352"], "tbench": 0}, competition=7, tid=17, fmt="classic",
    )

    assert inputs.bench_size == 16


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
