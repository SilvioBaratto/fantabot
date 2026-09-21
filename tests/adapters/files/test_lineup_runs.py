"""The scheduled lineup's run record: one line per run, and it must outlive a dead database.

Every `lineup submit --scheduled` appends one row, and the app reads them back. The runs
most worth recording are the ones that fail *before* anything else works — the bundled
Postgres not up after a reboot, a token that no longer opens — so the record cannot live in
Postgres, and this module joins `CAPTURE` in `tests/application/test_aste_outage.py`: it is
structurally barred from reaching persistence, not merely written not to.

The path is derived from the home directory, never from `./data`. `config.journal_path()`
is relative and says so — writer and reader agree only when both started from the
repository root — and the app is started from wherever its launcher was.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _paths import FIXTURES

from fantabot.adapters.files.lineup_runs import (
    FAILED,
    SKIPPED,
    SUBMITTED,
    LineupRejection,
    LineupRun,
    LineupShadow,
    append_run,
    read_runs,
)


def _run(**over: object) -> LineupRun:
    fields: dict[str, object] = {
        "at": "2026-09-12T10:00:00+02:00",
        "league": 4103937,
        "scheduled": True,
        "status": SUBMITTED,
        "module": "3421",
        "matchday": 3,
        "serie_a_matchday": 5,
        "starters": ("Mandas", "Bremer"),
        "bench": ("Caprile",),
    }
    fields.update(over)
    return LineupRun(**fields)  # type: ignore[arg-type]


def test_a_run_round_trips(tmp_path: Path) -> None:
    log = tmp_path / "lineup_runs.jsonl"
    run = _run(rejected=("4141 (LUP009)",), detail="")

    assert append_run(log, run) is True
    runs, skipped = read_runs(log)

    assert skipped == 0
    assert runs == (run,)


def test_runs_come_back_newest_first(tmp_path: Path) -> None:
    """A history is read from its end: the run that matters is the last one."""
    log = tmp_path / "lineup_runs.jsonl"
    append_run(log, _run(at="2026-09-12T09:00:00+02:00"))
    append_run(log, _run(at="2026-09-12T10:00:00+02:00", status=SKIPPED))

    runs, _ = read_runs(log)

    assert [r.at for r in runs] == ["2026-09-12T10:00:00+02:00", "2026-09-12T09:00:00+02:00"]


def test_a_torn_line_is_skipped_and_counted(tmp_path: Path) -> None:
    """`launchd` can kill a run mid-write; the history must not vanish over one bad line."""
    log = tmp_path / "lineup_runs.jsonl"
    append_run(log, _run())
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-09-12T11:0')

    runs, skipped = read_runs(log)

    assert len(runs) == 1 and skipped == 1


def test_no_file_is_no_runs(tmp_path: Path) -> None:
    assert read_runs(tmp_path / "absent.jsonl") == ((), 0)


def test_a_record_that_cannot_be_written_says_so_and_does_not_raise(tmp_path: Path) -> None:
    """The lineup has already gone in, or already failed, by the time this runs. A record
    that raised would turn a submitted lineup into a crashed command — so it reports."""
    blocked = tmp_path / "is_a_directory"
    blocked.mkdir()

    assert append_run(blocked, _run(status=FAILED, code="TokenMissing")) is False


# --- record v2 ------------------------------------------------------------------------


#: Three lines copied verbatim from the live `~/.fantabot/lineup_runs.jsonl` on 2026-09-21,
#: all 12-key v1: a skip past the start, and two `LUP009` walk-downs before T02's fix.
V1_LINES = FIXTURES / "lineup_runs" / "v1.jsonl"


class _F32:
    """A stand-in for `np.float32`: a number to `float()`, and not one to `json.dumps`."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __float__(self) -> float:
        return self.value


class _I64:
    """A stand-in for `np.int64`: an integer to `int()`, and not one to `json.dumps`."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __index__(self) -> int:
        return self.value

    def __int__(self) -> int:
        return self.value


def _shadow(**over: object) -> LineupShadow:
    fields: dict[str, object] = {
        "model": "projection",
        "module": "3412",
        "starter_ids": (6482, 2788, 7564),
        "bench_ids": (4360, 5750),
        "starters": ("Mandas", "Bremer", "Leysen F."),
        "bench": ("Caprile", "Baturina"),
        "e_pts": 1.62,
        "p_wdl": (0.45, 0.27, 0.28),
        "e_fp": 71.5,
        "sd": 6.25,
        "cuts": ("M 64 -> 32",),
    }
    fields.update(over)
    return LineupShadow(**fields)  # type: ignore[arg-type]


def _v2(**over: object) -> LineupRun:
    return _run(
        starter_ids=(6482, 2788),
        bench_ids=(4360,),
        competition=311681,
        tid=5921,
        model="indexcompare",
        rejections=(
            LineupRejection(
                module="4141",
                code="LUP009",
                message="The formation module is not allowed.",
                starter_ids=(6482, 2788),
                starters=("Mandas", "Bremer"),
            ),
        ),
        rejected=("4141 (LUP009)",),
        **over,
    )


def test_v1_lines_parse_with_every_new_field_at_its_default() -> None:
    """The history the app shows is mostly v1 lines, and will be for a season. A reader that
    required the new keys would turn every one of them into a skipped line."""
    runs, skipped = read_runs(V1_LINES)

    assert skipped == 0 and len(runs) == 3
    for run in runs:
        assert run.starter_ids == () and run.bench_ids == ()
        assert run.competition is None and run.tid is None
        assert run.model == "", "an old line must not claim a model it never recorded"
        assert run.fallback == "" and run.warnings == ()
        assert run.rejections == () and run.shadow is None
    newest = runs[0]
    assert newest.module == "3412" and len(newest.rejected) == 7


def test_a_populated_run_and_shadow_round_trip_equal(tmp_path: Path) -> None:
    log = tmp_path / "lineup_runs.jsonl"
    run = _v2(fallback="voti@3 < 5", warnings=("g16 6/10 (postponed?)",), shadow=_shadow())

    assert append_run(log, run) is True
    runs, skipped = read_runs(log)

    assert skipped == 0
    assert runs == (run,)


def test_numpy_scalars_are_coerced_to_native_values_and_round_trip(tmp_path: Path) -> None:
    """The projection hands back numpy scalars, which `json.dumps` refuses. Coerced at
    construction, the shadow is plain Python before anything tries to write it."""
    log = tmp_path / "lineup_runs.jsonl"
    shadow = _shadow(
        starter_ids=(_I64(6482), _I64(2788)),
        bench_ids=[_I64(4360)],
        e_pts=_F32(1.5),
        p_wdl=[_F32(0.5), _F32(0.25), _F32(0.25)],
        e_fp=_F32(70.0),
        sd=_F32(6.0),
    )

    assert shadow.starter_ids == (6482, 2788) and type(shadow.starter_ids[0]) is int
    assert shadow.bench_ids == (4360,)
    assert shadow.p_wdl == (0.5, 0.25, 0.25) and type(shadow.p_wdl[0]) is float
    assert type(shadow.e_pts) is float and type(shadow.sd) is float

    run = _v2(shadow=shadow)
    assert append_run(log, run) is True
    assert read_runs(log) == ((run,), 0)


def test_an_unserializable_shadow_still_writes_the_record(tmp_path: Path) -> None:
    """The run is the record; the shadow is a comparison riding on it. A NaN is a float and
    survives coercion, but it is not JSON — the app's response would refuse it."""
    log = tmp_path / "lineup_runs.jsonl"
    run = _v2(warnings=("g16 6/10 (postponed?)",), shadow=_shadow(e_pts=float("nan")))

    assert append_run(log, run) is True
    (back,), skipped = read_runs(log)

    assert skipped == 0
    assert back.shadow is None
    assert back.warnings == ("g16 6/10 (postponed?)", "shadow not written: ValueError")
    assert back.status == run.status and back.starter_ids == run.starter_ids
    assert back.rejections == run.rejections


def test_a_float_id_is_refused_not_truncated() -> None:
    """`int(6482.9)` is 6482: a truncated id names a different player and nothing raises."""
    with pytest.raises(TypeError):
        _shadow(starter_ids=(6482.9,))
