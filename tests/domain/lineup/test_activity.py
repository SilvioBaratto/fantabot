"""`activity` — which leghe to field a lineup in. Pure; one test per state."""

from __future__ import annotations

from fantabot.domain.lineup.activity import Activity, competition_activity, lineup_activity

TID = 19851533


def _c(cid: int, *, s: int = 4, e: int = 38, deleted: bool = False, kind: int = 1,
       tids: list[int] | None = None) -> dict[str, object]:
    return {"id": cid, "sDay": s, "eDay": e, "del": deleted, "type": kind,
            "tmids": [TID] if tids is None else tids}


def test_no_competition_at_all_is_inactive() -> None:
    # 3012550 / 3576298 / 4344672 on 2026-09-22: an empty list.
    a = competition_activity([], tid=TID, serie_a_matchday=6)

    assert a.state == "no_competition" and not a.active


def test_a_deleted_or_foreign_competition_does_not_count() -> None:
    comps = [_c(1, deleted=True), _c(2, tids=[1, 2])]

    assert competition_activity(comps, tid=TID, serie_a_matchday=6).state == "no_competition"


def test_every_competition_over_is_finished() -> None:
    a = competition_activity([_c(1, e=5)], tid=TID, serie_a_matchday=6)

    assert a.state == "finished"


def test_every_competition_ahead_is_not_started() -> None:
    a = competition_activity([_c(1, s=10)], tid=TID, serie_a_matchday=6)

    assert a.state == "not_started" and "10" in a.reason


def test_a_championship_and_a_cup_resolve_to_the_championship() -> None:
    # 2761635: Campionato (type 1) + Coppa (type 4).
    a = competition_activity([_c(579261), _c(579295, kind=4)], tid=TID, serie_a_matchday=6)

    assert a.state == "idle" and a.competition == 579261


def test_two_championships_are_ambiguous() -> None:
    a = competition_activity([_c(1), _c(2)], tid=TID, serie_a_matchday=6)

    assert a.state == "ambiguous" and not a.active


def test_a_lineup_with_coordinates_is_open() -> None:
    pending = Activity("idle", "competition live", competition=645700)

    a = lineup_activity(pending, {"mday": 3, "cmday": 6})

    assert a.state == "open" and a.active and a.competition == 645700


def test_a_lineup_without_coordinates_is_idle_but_active() -> None:
    pending = Activity("idle", "competition live", competition=645700)

    a = lineup_activity(pending, {"mday": 0, "cmday": 0})

    assert a.state == "idle" and a.active
