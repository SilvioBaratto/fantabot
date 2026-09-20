"""`GET /db/dump/target` and `POST /db/dump` (T26).

**The path is the deliverable, and that is a rule rather than a style.** `SPEC.md` §8
Never #4 — *no browser download of a database dump* — is the one thing this route must
not grow. A dump carries the `league_tokens` rows, encrypted but still credentials, and
handing over the bytes puts the file wherever the browser puts downloads: a directory
covered by neither the `/Volumes/` refusal nor `.gitignore`'s `*.dump`. So the route
names the path and the operator goes and looks.

**The GET exists because the refusal is worth having before the run, not after.** A dump
of this database is 1893 MB and minutes. A button that only discovers on click that
`$HOME` is on the volume the dump exists to survive the loss of has reproduced exactly
the failure the guard is for.

**A supervised child, not a request**, for `test_db_scrape.py`'s reason and more of it:
minutes of streaming, where that one is minutes of polite GETs.

No socket and no `pg_dump`: `fantabot_command` is replaced with a short-lived child that
prints its argv, which is where a route that grew its own dump loop would stop being
observable.
"""

from __future__ import annotations

import sys
import time

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app

skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="`/Volumes/` is where macOS mounts external volumes; Windows mounts nothing there",
)


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """A short-lived real child in place of the CLI, and a `$HOME` that is not the real one."""
    from fantabot_app.api.infrastructure import processes

    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def frozen_day(monkeypatch):
    """Freeze the date. The filename is a date, so every assertion here would move daily."""
    from datetime import date

    from fantabot_app.api.v1.endpoints import dump

    monkeypatch.setattr(dump, "_today", lambda: date(2026, 9, 20))


# -- GET /db/dump/target ----------------------------------------------------------------


def test_the_target_is_named_before_anything_runs(quick_child, frozen_day) -> None:
    body = TestClient(app).get("/api/v1/db/dump/target").json()

    assert body["path"] == str(quick_child / "fantabot-db-20260920.dump")
    assert body["refused"] == ""


def test_the_target_says_nothing_is_there_yet(quick_child, frozen_day) -> None:
    body = TestClient(app).get("/api/v1/db/dump/target").json()

    assert body["exists"] is False
    assert body["size_bytes"] is None


def test_a_dump_already_taken_today_is_reported_with_its_size(quick_child, frozen_day) -> None:
    """A second dump on the same day overwrites the first, so the screen has to say so."""
    (quick_child / "fantabot-db-20260920.dump").write_bytes(b"PGDMP" + b"x" * 95)

    body = TestClient(app).get("/api/v1/db/dump/target").json()

    assert body["exists"] is True
    assert body["size_bytes"] == 100


@skip_on_windows
def test_a_home_on_an_external_volume_offers_no_path_at_all(monkeypatch) -> None:
    """Not a disabled button beside a path: there is no path, and the reason is the answer."""
    monkeypatch.setenv("HOME", "/Volumes/External SSD/home")

    body = TestClient(app).get("/api/v1/db/dump/target").json()

    assert body["path"] == ""
    assert "/Volumes/External SSD/home/" in body["refused"]


# -- POST /db/dump ----------------------------------------------------------------------


def test_a_dump_spawns_the_command_and_names_where_it_will_land(
    quick_child, frozen_day
) -> None:
    client = TestClient(app)

    started = client.post("/api/v1/db/dump").json()

    assert started["outcome"] == "started"
    assert started["path"] == str(quick_child / "fantabot-db-20260920.dump")
    assert _wait(lambda: _job(client, started["job_id"])["status"] == "done")
    assert "db dump" in " ".join(_job(client, started["job_id"])["lines"])


def test_the_job_says_what_kind_it_is(quick_child, frozen_day) -> None:
    client = TestClient(app)
    started = client.post("/api/v1/db/dump").json()

    assert _wait(lambda: _job(client, started["job_id"])["status"] == "done")
    kinds = {job["id"]: job["kind"] for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert kinds[started["job_id"]] == "db-dump"


def test_the_job_is_stoppable(quick_child, frozen_day) -> None:
    """Minutes of streaming. A stop removes the partial file rather than leaving a lie —
    `application/db_dump.run_dump`, which is what makes offering the stop honest."""
    client = TestClient(app)
    started = client.post("/api/v1/db/dump").json()

    listed = {job["id"]: job for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert listed[started["job_id"]]["stoppable"] is True


@skip_on_windows
def test_a_refused_target_spawns_nothing(monkeypatch) -> None:
    """The refusal is the whole point of deriving the path before the child is started."""
    monkeypatch.setenv("HOME", "/Volumes/External SSD/home")
    client = TestClient(app)
    before = len(client.get("/api/v1/jobs").json()["jobs"])

    body = client.post("/api/v1/db/dump").json()

    assert body["outcome"] == "refused"
    assert body["job_id"] == ""
    assert "/Volumes/External SSD/home/" in body["detail"]
    assert len(client.get("/api/v1/jobs").json()["jobs"]) == before


def test_the_outcomes_are_exactly_the_two_declared() -> None:
    """The ratchet `api/outcomes.py` describes: a route that gains one must say so."""
    from fantabot_app.api.v1.endpoints.dump import DUMP_OUTCOMES

    assert DUMP_OUTCOMES == ("started", "refused")


def test_the_route_never_hands_over_the_bytes(quick_child, frozen_day) -> None:
    """`SPEC.md` §8 Never #4, as a test rather than as a sentence in a docstring.

    Structural on purpose. A response assertion only proves that *this* call returned
    JSON; what the rule forbids is the route ever learning to stream the file, and the
    names below are the only ways FastAPI knows how.
    """
    from pathlib import Path

    import fantabot_app.api.v1.endpoints.dump as module

    source = Path(module.__file__).read_text()
    for forbidden in ("FileResponse", "StreamingResponse", "send_file", "attachment"):
        assert forbidden not in source, f"{forbidden} would hand over the dump's bytes"

    response = TestClient(app).post("/api/v1/db/dump")
    assert "content-disposition" not in {k.lower() for k in response.headers}
