"""`POST /asta/room/watch` — the room, supervised, and unable to arm.

The first asta job the app starts. It runs `fantabot asta room <id>` as a child process
(`infrastructure/processes.py`), which is the whole of what "closing the tab does not stop
the watch" means: a subprocess outlives the request that spawned it, and `GET /jobs` is how
the reopened tab finds it again.

Three properties are tested here rather than assumed:

* **It cannot arm.** `--arm` is `asta room`'s second lock and the app never sends it. Tested
  on the built argv *and* on this endpoint's own source, because the argv assertion only
  covers the request shape that exists today.
* **It names none of the value model's numbers.** `--lam`, `--budget` and the three alphas
  are the child's own option set; a copy here is the second value model that
  `application/asta_planner.py` exists to prevent.
* **A link that is not a room is refused before anything is spawned**, with the parser's
  own message — "started, then died two seconds later" is not a refusal an operator reads.
"""

from __future__ import annotations

import sys
import time

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app

#: A real-shaped fantaleague id. `parse_room_url` takes a bare uuid or a room link.
ROOM = "8ca35cbf-0f7a-4b3a-9e2e-3f2a1b0c4d5e"


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """The supervisor pointed at a short-lived real child that echoes its argv.

    `test_harvest.py`'s fixture, for its reason: a fake `Popen` would agree with whatever
    the implementation happened to do. `FANTABOT_DATA_DIR` moves the stop flag (and the
    journal) into `tmp_path`, so a run leaves nothing in the repository's `data/`.
    """
    from fantabot_app.api.infrastructure import processes

    monkeypatch.setenv("FANTABOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )
    return tmp_path


def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _wait(done, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(0.05)
    return False


def _watch(client: TestClient, url: str = ROOM):
    return client.post("/api/v1/asta/room/watch", json={"url": url})


def _watches(client: TestClient) -> int:
    """How many watches the process-wide registry holds. A refusal must not move it."""
    return len(
        [row for row in client.get("/api/v1/jobs").json()["jobs"] if row["kind"] == "asta-watch"]
    )


def test_a_watch_shows_up_in_the_job_list_and_can_be_stopped(quick_child) -> None:
    """The reattach path. A tab that closed finds its watch here and does not start a second."""
    client = TestClient(app)

    job_id = _watch(client).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    row = next(row for row in client.get("/api/v1/jobs").json()["jobs"] if row["id"] == job_id)
    assert row["kind"] == "asta-watch"
    assert row["stoppable"] is True


def test_the_supervised_command_is_the_room_named_by_its_id(quick_child) -> None:
    """The parsed id, not the pasted string: one spelling reaches the child and the flag."""
    client = TestClient(app)

    job_id = _watch(client, f"https://app.fantalab.it/asta?asta={ROOM}").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert f"asta room {ROOM}" in " ".join(_job(client, job_id)["lines"])


def test_the_watch_cannot_arm(quick_child) -> None:
    """`--arm` is the lock the operator opens deliberately, for one room, at the keyboard.

    Asserted twice. The argv covers the request shape that exists today; the source scan
    covers the one somebody adds an `arm` field to tomorrow.
    """
    from pathlib import Path

    from fantabot_app.api.v1.endpoints import room as endpoint

    client = TestClient(app)
    job_id = _watch(client).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert "--arm" not in " ".join(_job(client, job_id)["lines"])
    assert "--arm" not in Path(endpoint.__file__).read_text(encoding="utf-8")


def test_the_watch_names_none_of_the_value_models_numbers(quick_child) -> None:
    """The child's own option set is the single copy — three commands once held three."""
    client = TestClient(app)

    job_id = _watch(client).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    log = " ".join(_job(client, job_id)["lines"])
    named = [
        flag
        for flag in ("--lam", "--budget", "--ceiling-alpha", "--bargain-beta", "--bargain-share")
        if flag in log
    ]
    assert named == [], f"the app declares the value model's numbers: {named}"


def test_the_copilot_is_off_because_nothing_reads_its_pane(quick_child) -> None:
    """It renders into a Rich screen the app never sees, and every brief is a real call."""
    client = TestClient(app)

    job_id = _watch(client).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert "--no-copilot" in " ".join(_job(client, job_id)["lines"])


def test_a_link_that_is_not_a_room_is_refused_before_anything_is_spawned(quick_child) -> None:
    client = TestClient(app)
    before = _watches(client)

    response = _watch(client, "https://leghe.fantacalcio.it/legamiallerotaie2")

    assert response.status_code == 400
    assert "app.fantalab.it/asta?asta=" in response.json()["detail"]
    # Counted, never "the registry is empty": it is process-wide and holds every watch
    # this module has already started.
    assert _watches(client) == before


def test_an_invitation_link_is_refused_in_its_own_words(quick_child) -> None:
    """What an admin actually sends. Its message already says what to paste instead."""
    response = _watch(TestClient(app), "https://app.fantalab.it/join-asta?invitation_id=abc")

    assert response.status_code == 400
    assert "invit" in response.json()["detail"].lower()


def test_each_room_gets_its_own_stop_flag(quick_child) -> None:
    """One job per flag, which is the caller's to keep — `ProcessJob`'s own contract.

    A flag shared between two rooms would let a stop aimed at either stop the other.
    """
    from fantabot_app.api.v1.endpoints.room import watch_flag

    first, second = watch_flag(ROOM), watch_flag("0000-other")

    assert first != second
    assert first.parent == second.parent
