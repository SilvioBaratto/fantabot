"""`GET /lineup/runs` — the scheduled lineup's history, read back. The app writes nothing here.

The operator chose to learn how the unattended lineup went from this screen rather than from
notifications, so it has to say three things a green list alone cannot: that a run
**failed**, that the record could not be **read**, and that **no run happened at all** — the
Mac off, the job unloaded, macOS refusing it the external SSD. That last one writes nothing,
so it is inferred from the age of the newest row.

The file is the CLI's (`adapters/files/lineup_runs.py`), and its path is home-derived, not
`./data`-relative: the `launchd` job starts in the repository and this server starts wherever
its launcher was.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fantabot.adapters.files.lineup_runs import FAILED, SKIPPED, SUBMITTED, LineupRun, append_run
from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.lineup import STALE_AFTER_HOURS, read_lineup_runs

from .conftest import redirect_home

CEST = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 12, 20, 0, tzinfo=CEST)


def _run(at: datetime, **over: object) -> LineupRun:
    fields: dict[str, object] = {
        "at": at.isoformat(timespec="seconds"),
        "league": 4103937,
        "scheduled": True,
        "status": SUBMITTED,
        "module": "3421",
        "matchday": 3,
        "serie_a_matchday": 5,
        "starters": ("Mandas",),
        "bench": ("Caprile",),
    }
    fields.update(over)
    return LineupRun(**fields)  # type: ignore[arg-type]


def test_a_missing_record_is_not_an_error_and_names_the_path(tmp_path: Path) -> None:
    """No runs yet is a neutral screen, not a red one — and it says which file it looked for."""
    page = read_lineup_runs(tmp_path / "lineup_runs.jsonl", now=NOW)

    assert page.ok is True and page.exists is False
    assert page.path.endswith("lineup_runs.jsonl")
    assert page.runs == [] and page.stale is False


def test_runs_come_back_newest_first_field_for_field(tmp_path: Path) -> None:
    log = tmp_path / "lineup_runs.jsonl"
    append_run(log, _run(NOW - timedelta(hours=2)))
    append_run(
        log,
        _run(
            NOW - timedelta(hours=1), status=FAILED, code="TokenMissing",
            detail="no stored token for lega 4103937", module="", starters=(), bench=(),
        ),
    )

    page = read_lineup_runs(log, now=NOW)

    assert page.total == 2
    assert [r.status for r in page.runs] == [FAILED, SUBMITTED]
    assert page.runs[0].code == "TokenMissing"
    assert page.runs[0].detail == "no stored token for lega 4103937"
    assert page.runs[1].starters == ["Mandas"] and page.runs[1].serie_a_matchday == 5


def test_a_torn_line_is_skipped_and_counted(tmp_path: Path) -> None:
    log = tmp_path / "lineup_runs.jsonl"
    append_run(log, _run(NOW))
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-09-12T2')

    page = read_lineup_runs(log, now=NOW)

    assert page.total == 1 and page.skipped == 1


def test_an_unreadable_record_is_its_own_answer(tmp_path: Path) -> None:
    """Not "no runs yet": that would send the operator looking for a path that is right."""
    blocked = tmp_path / "lineup_runs.jsonl"
    blocked.mkdir()

    page = read_lineup_runs(blocked, now=NOW)

    assert page.ok is False and page.error


def test_the_limit_bounds_the_page_not_the_total(tmp_path: Path) -> None:
    log = tmp_path / "lineup_runs.jsonl"
    for hours in (3, 2, 1):
        append_run(log, _run(NOW - timedelta(hours=hours)))

    page = read_lineup_runs(log, now=NOW, limit=2)

    assert page.total == 3 and len(page.runs) == 2


class TestNoRunAtAllIsAFailureToo:
    """A job that never runs writes no row, so a list of green rows from last week reads as
    fine. The age of the newest row is the only signal there is."""

    def test_a_newest_run_older_than_the_threshold_is_stale(self, tmp_path: Path) -> None:
        log = tmp_path / "lineup_runs.jsonl"
        append_run(log, _run(NOW - timedelta(hours=STALE_AFTER_HOURS + 1)))

        page = read_lineup_runs(log, now=NOW)

        assert page.stale is True
        assert page.last_age_hours == pytest.approx(STALE_AFTER_HOURS + 1, abs=0.1)

    def test_a_normal_night_is_not_stale(self, tmp_path: Path) -> None:
        """The job fires through the day, not overnight: ~9 hours without a run is normal."""
        log = tmp_path / "lineup_runs.jsonl"
        append_run(log, _run(NOW - timedelta(hours=9)))

        assert read_lineup_runs(log, now=NOW).stale is False

    def test_the_age_is_the_newest_run_s(self, tmp_path: Path) -> None:
        log = tmp_path / "lineup_runs.jsonl"
        append_run(log, _run(NOW - timedelta(days=3)))
        append_run(log, _run(NOW - timedelta(hours=1)))

        page = read_lineup_runs(log, now=NOW)

        assert page.stale is False
        assert page.last_age_hours == pytest.approx(1.0, abs=0.1)


def test_the_home_redirect_works_under_both_resolvers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper below is a platform assumption if it only satisfies this platform.

    `ntpath.expanduser` is a pure function and runs fine here, so Windows' answer is
    checkable from macOS — which is the only reason this defect is fixable without a
    Windows machine. Setting `HOME` alone leaves it returning a literal `~`.
    """
    import ntpath
    import posixpath

    redirect_home(monkeypatch, tmp_path)

    assert posixpath.expanduser("~") == str(tmp_path), "POSIX reads HOME"
    assert ntpath.expanduser("~") == str(tmp_path), (
        "Windows reads USERPROFILE and ignores HOME, so a redirect that sets only HOME "
        "points the route at the operator's real home"
    )
    assert Path.home() == tmp_path


def test_the_route_reads_the_home_derived_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the router, at the path the CLI writes to."""
    redirect_home(monkeypatch, tmp_path)
    append_run(
        tmp_path / ".fantabot" / "lineup_runs.jsonl",
        _run(NOW, status=SKIPPED, code="matchday-started", detail="matchday 4 started"),
    )

    with TestClient(app) as client:
        body = client.get("/api/v1/lineup/runs").json()

    assert body["ok"] is True and body["total"] == 1
    assert body["runs"][0]["status"] == SKIPPED and body["runs"][0]["code"] == "matchday-started"
