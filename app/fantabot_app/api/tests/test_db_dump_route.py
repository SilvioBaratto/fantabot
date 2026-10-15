"""`GET /db/dump/target` and `POST /db/dump` (T26).

**The path is the deliverable, and that is a rule rather than a style.** The archived parity-phase spec §8
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

No socket and no `pg_dump`: `fantabot_command` is replaced with a child that prints its
argv, which is where a route that grew its own dump loop would stop being observable. Where
a dump has to be in flight, the child waits for a release file rather than a stop, because a
stop on Windows sends no signal and a child that only a signal ends would outlive its test.
"""

from __future__ import annotations

import sys

import pytest
from fantabot.application import db_dump
from fastapi.testclient import TestClient

from fantabot_app.api.infrastructure import processes
from fantabot_app.api.infrastructure.jobs import registry
from fantabot_app.api.main import app

from .conftest import job as _job
from .conftest import redirect_home, stub_child_command
from .conftest import wait_for as _wait

skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="`/Volumes/` is where macOS mounts external volumes; Windows mounts nothing there",
)

#: A prefix no resolved path starts with, standing in for `/Volumes/` where a test is about
#: the route and not about that refusal.
NO_SUCH_MOUNT = "/\0no-such-mount/"

#: A child that stays up until a file appears. Run from a file rather than `-c` so the
#: multi-line body does not depend on how a platform quotes a newline into a command line.
LINGER = (
    "import pathlib, sys, time\n"
    "release = pathlib.Path(sys.argv[1])\n"
    "while not release.exists():\n"
    "    time.sleep(0.02)\n"
)


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """A short-lived real child in place of the CLI, and a `$HOME` that is not the real one."""
    stub_child_command(monkeypatch)
    redirect_home(monkeypatch, tmp_path)
    # `tmp_path` lives wherever TMPDIR points, and `dump_target` refuses anything under
    # `/Volumes/`: on a Mac whose TMPDIR is on an external volume, every test using this
    # home would be refused before it began. The refusal has its own tests, below and in
    # tests/application/test_db_dump.py, and neither goes through this fixture.
    monkeypatch.setattr(db_dump, "EXTERNAL_VOLUME_PREFIX", NO_SUCH_MOUNT)
    return tmp_path


@pytest.fixture
def lingering_child(quick_child, monkeypatch):
    """A dump that is still running until the test says otherwise. Yields the file that ends it.

    It ends by a file, never by a stop, because what a stop does differs by platform. On
    POSIX the supervisor sends `SIGINT`; on Windows the first stop only writes the flag
    `ProcessJob` documents, and a child that never reads it keeps running. A child that
    ended only on a stop would still be `running` when its test ended there, and since the
    registry is process-global the next test's `POST /db/dump` would be refused as
    "already running" — a failure two tests away from its cause.
    """
    release = quick_child / "release"
    script = quick_child / "linger.py"
    script.write_text(LINGER, encoding="utf-8")
    monkeypatch.setattr(
        processes, "fantabot_command", lambda *args: [sys.executable, str(script), str(release)]
    )
    yield release
    release.touch()


def _dumps_in_flight() -> list[str]:
    return [j.id for j in registry.list() if j.kind == "db-dump" and j.status == "running"]


@pytest.fixture(autouse=True)
def no_dump_outlives_its_test():
    """Fail the test that left a dump running, not the next one that tries to start one.

    `POST /db/dump` refuses while any `db-dump` job runs, and that registry is shared by the
    whole process. A job left behind surfaces as an unrelated test's refusal, and the
    refusal's body says only "a dump is already running".
    """
    yield
    assert _wait(lambda: not _dumps_in_flight()), (
        f"db-dump job(s) still running when the test ended: {_dumps_in_flight()}"
    )


def _start(client: TestClient) -> dict[str, str]:
    """`POST /db/dump`, which must have started a job; the body is the failure when it did not.

    A refusal is a `200` with an empty `job_id`, and `GET /jobs/` redirects to the job list,
    so a test that read the job's `status` next would die on `KeyError: 'status'` and name
    nothing.
    """
    body: dict[str, str] = client.post("/api/v1/db/dump").json()
    assert body["outcome"] == "started" and body["job_id"], (
        f"POST /db/dump did not start a job: {body}"
    )
    return body


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
    redirect_home(monkeypatch, "/Volumes/External SSD/home")

    body = TestClient(app).get("/api/v1/db/dump/target").json()

    assert body["path"] == ""
    assert "/Volumes/External SSD/home/" in body["refused"]


# -- POST /db/dump ----------------------------------------------------------------------


def test_a_dump_spawns_the_command_and_names_where_it_will_land(
    quick_child, frozen_day
) -> None:
    client = TestClient(app)

    started = _start(client)

    assert started["path"] == str(quick_child / "fantabot-db-20260920.dump")
    assert _wait(lambda: _job(client, started["job_id"])["status"] == "done")
    assert "db dump" in " ".join(_job(client, started["job_id"])["lines"])


def test_the_job_says_what_kind_it_is(quick_child, frozen_day) -> None:
    client = TestClient(app)
    started = _start(client)

    assert _wait(lambda: _job(client, started["job_id"])["status"] == "done")
    kinds = {job["id"]: job["kind"] for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert kinds[started["job_id"]] == "db-dump"


def test_the_job_is_stoppable(quick_child, frozen_day) -> None:
    """Minutes of streaming. A stop removes the partial file rather than leaving a lie —
    `application/db_dump.run_dump`, which is what makes offering the stop honest."""
    client = TestClient(app)
    started = _start(client)

    listed = {job["id"]: job for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert listed[started["job_id"]]["stoppable"] is True

    # The registry is process-global; a job left running here would read as a dump in flight.
    client.post(f"/api/v1/jobs/{started['job_id']}/stop")
    assert _wait(lambda: _job(client, started["job_id"])["status"] != "running")


@skip_on_windows
def test_a_refused_target_spawns_nothing(monkeypatch) -> None:
    """The refusal is the whole point of deriving the path before the child is started."""
    redirect_home(monkeypatch, "/Volumes/External SSD/home")
    client = TestClient(app)
    before = len(client.get("/api/v1/jobs").json()["jobs"])

    body = client.post("/api/v1/db/dump").json()

    assert body["outcome"] == "refused"
    assert body["job_id"] == ""
    assert "/Volumes/External SSD/home/" in body["detail"]
    assert len(client.get("/api/v1/jobs").json()["jobs"]) == before


def test_a_second_dump_is_refused_while_one_runs(lingering_child, frozen_day) -> None:
    """Both would write the one dated file; the second must not start a competing child."""
    client = TestClient(app)
    first = _start(client)
    assert _job(client, first["job_id"])["status"] == "running"
    before = len(client.get("/api/v1/jobs").json()["jobs"])

    second = client.post("/api/v1/db/dump").json()

    assert second["outcome"] == "refused"
    assert second["job_id"] == ""
    assert "already" in second["detail"]
    assert len(client.get("/api/v1/jobs").json()["jobs"]) == before


def test_a_dump_may_start_again_once_the_last_one_finished(quick_child, frozen_day) -> None:
    client = TestClient(app)
    first = _start(client)
    assert _wait(lambda: _job(client, first["job_id"])["status"] == "done")

    second = client.post("/api/v1/db/dump").json()

    assert second["outcome"] == "started", second
    assert _wait(lambda: _job(client, second["job_id"])["status"] == "done")


def test_the_outcomes_are_exactly_the_two_declared() -> None:
    """The ratchet `api/outcomes.py` describes: a route that gains one must say so."""
    from fantabot_app.api.v1.endpoints.dump import DUMP_OUTCOMES

    assert DUMP_OUTCOMES == ("started", "refused")


def test_the_route_never_hands_over_the_bytes(quick_child, frozen_day) -> None:
    """The archived parity-phase spec §8 Never #4, as a test rather than as a sentence in a docstring.

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
