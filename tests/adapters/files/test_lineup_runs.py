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

from fantabot.adapters.files.lineup_runs import (
    FAILED,
    SKIPPED,
    SUBMITTED,
    LineupRun,
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
