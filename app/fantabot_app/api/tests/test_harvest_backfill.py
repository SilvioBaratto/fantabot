"""`GET /harvest/backfill/candidates` and `POST /harvest/backfill` (T22).

The first place the app has wanted a **file path** from the operator, and the reason the
spec says a picker rather than free text: naming a harvest path explicitly *creates* what
it cannot find, which is how a second landing zone with its own checkpoint and its own
fold state appears. Three stray files and two spare seeds already sit in the real home
from exactly that.

So the route takes a **name**, never a path, and refuses any name the candidate list does
not already hold. That is one rule doing two jobs: the operator cannot invent a landing
zone, and `../../.ssh/id_rsa` is not a candidate either.

**The dry-run-then-write sequencing is the page's, not this route's.** `harvest backfill`
takes `--dry-run` as a flag in either order, and a route that refused a write without a
preceding dry run would give the app a restriction the CLI has not got — the wrong
direction, but still a divergence. What the route enforces is what the command enforces.
"""

from __future__ import annotations

import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _log(home, name: str, *, lines: int = 1):
    path = home / name
    path.write_text(
        "".join(
            json.dumps({"seen_at": "2026-08-26T18:21:05+00:00", "auction_id": "a", "state": {}})
            + "\n"
            for _ in range(lines)
        )
    )
    return path


def _seed(home, name: str, rows: int = 1):
    path = home / name
    path.write_text(json.dumps([["a", "15", 10, 500, 25, 25, "r", "f", 7, 7, "L"]] * rows))
    return path


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A harvest home shaped like the real one, with the live zone and a recorded evening."""
    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(tmp_path))
    _log(tmp_path, "live.jsonl", lines=3)
    _log(tmp_path, "events_2026-08-26.jsonl", lines=2)
    _seed(tmp_path, "seed.json", rows=4)
    _seed(tmp_path, "seed_2026-08-26.json", rows=2)
    (tmp_path / "live.jsonl.offset").write_text("123")
    return tmp_path


@pytest.fixture
def quick_child(monkeypatch, home):
    """A short-lived real child in place of the CLI, so argv is observable in the log."""
    from fantabot_app.api.infrastructure import processes

    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )
    return home


# -- GET /harvest/backfill/candidates -------------------------------------------------


def test_the_picker_lists_the_logs_and_the_seeds_apart(home) -> None:
    body = TestClient(app).get("/api/v1/harvest/backfill/candidates").json()

    assert body["exists"] is True
    assert [log["name"] for log in body["logs"]] == [
        "events_2026-08-26.jsonl",
        "live.jsonl",
    ]
    assert [seed["name"] for seed in body["seeds"]] == ["seed.json", "seed_2026-08-26.json"]
    assert body["home"] == str(home)


def test_the_live_landing_zone_is_listed_and_flagged(home) -> None:
    logs = {log["name"]: log for log in TestClient(app).get(
        "/api/v1/harvest/backfill/candidates"
    ).json()["logs"]}

    assert logs["live.jsonl"]["live"] is True
    assert logs["events_2026-08-26.jsonl"]["live"] is False


def test_each_seed_carries_how_many_auctions_it_describes(home) -> None:
    """The one number that tells today's seed from a recorded evening's — and a mismatch
    is silent: every auction the chosen seed does not describe is dropped and the run
    still reports success."""
    seeds = {s["name"]: s for s in TestClient(app).get(
        "/api/v1/harvest/backfill/candidates"
    ).json()["seeds"]}

    assert seeds["seed.json"]["rows"] == 4
    assert seeds["seed_2026-08-26.json"]["rows"] == 2


def test_the_picker_degrades_open_when_there_is_no_home(monkeypatch, tmp_path) -> None:
    """A status read that 500s reports nothing about the one thing it is for, and
    `harvest_dir()` names a directory a fresh install has not created."""
    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(tmp_path / "never_made"))

    response = TestClient(app).get("/api/v1/harvest/backfill/candidates")

    assert response.status_code == 200
    assert response.json()["exists"] is False
    assert response.json()["logs"] == []


# -- POST /harvest/backfill — the name rule -------------------------------------------


def test_a_dry_run_spawns_the_command_with_the_chosen_pair(quick_child) -> None:
    client = TestClient(app)

    job_id = client.post(
        "/api/v1/harvest/backfill",
        json={
            "log": "events_2026-08-26.jsonl",
            "seed": "seed_2026-08-26.json",
            "asta_type": "classic",
            "dry_run": True,
        },
    ).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    log = " ".join(_job(client, job_id)["lines"])
    assert "harvest backfill" in log
    assert str(quick_child / "events_2026-08-26.jsonl") in log
    assert f"--seed {quick_child / 'seed_2026-08-26.json'}" in log
    assert "--asta-type classic" in log
    assert "--dry-run" in log


def test_a_write_carries_no_dry_run_flag(quick_child) -> None:
    client = TestClient(app)

    job_id = client.post(
        "/api/v1/harvest/backfill",
        json={"log": "live.jsonl", "seed": "seed.json", "dry_run": False},
    ).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert "--dry-run" not in " ".join(_job(client, job_id)["lines"])


def test_the_job_is_stoppable(quick_child) -> None:
    """A backfill over 144,518 states is minutes long, and a job the page cannot stop
    makes its Stop control a lie. It is lock-free — a backfill takes no landing-zone
    role — so it needs a stop flag of its own."""
    client = TestClient(app)

    job_id = client.post(
        "/api/v1/harvest/backfill", json={"log": "live.jsonl", "seed": "seed.json"}
    ).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] in {"running", "done"})
    row = next(row for row in client.get("/api/v1/jobs").json()["jobs"] if row["id"] == job_id)
    assert row["kind"] == "harvest-backfill"
    assert row["stoppable"] is True


def test_two_logs_get_two_stop_flags(quick_child) -> None:
    """One flag per input, derived from it — `stop_path`'s own reasoning. A flag shared
    between two backfills would let a stop aimed at either stop the other."""
    from fantabot_app.api.v1.endpoints.harvest import backfill_flag

    assert backfill_flag(quick_child / "live.jsonl") != backfill_flag(
        quick_child / "events_2026-08-26.jsonl"
    )


def test_a_log_that_is_not_a_candidate_is_refused_before_anything_is_spawned(
    quick_child,
) -> None:
    """`assignments_2026-08-26.jsonl` is a real file in the real home with the right
    extension and no `state`. The picker never offers it, and neither does the route."""
    (quick_child / "assignments_2026-08-26.jsonl").write_text(
        json.dumps({"auction_id": "a", "price": 903}) + "\n"
    )

    response = TestClient(app).post(
        "/api/v1/harvest/backfill",
        json={"log": "assignments_2026-08-26.jsonl", "seed": "seed.json"},
    )

    assert response.status_code == 400
    assert "assignments_2026-08-26.jsonl" in response.json()["detail"]


def test_a_path_is_not_a_name(quick_child) -> None:
    """The whole point of the picker. A route taking a path would let the app create the
    second landing zone the CLI's own flags already create by accident — and would read
    anything on the volume besides."""
    response = TestClient(app).post(
        "/api/v1/harvest/backfill",
        json={"log": "../../../etc/passwd", "seed": "seed.json"},
    )

    assert response.status_code == 400
    assert "candidate" in response.json()["detail"]


def test_a_seed_that_is_not_a_candidate_is_refused_too(quick_child) -> None:
    """Both halves, not just the log: a typed seed name is how the CLI's `--seed` creates
    an empty file it then cannot read."""
    response = TestClient(app).post(
        "/api/v1/harvest/backfill",
        json={"log": "live.jsonl", "seed": "tomorrow.json"},
    )

    assert response.status_code == 400
    assert "tomorrow.json" in response.json()["detail"]


def test_a_json_that_is_not_a_seed_is_refused_although_it_exists(quick_child) -> None:
    """What the candidate check catches and an existence check cannot.

    `listone_map.json` is in every harvest home, has the right extension, and is a JSON
    *object* — so `clean_backfill` finds it and is right to: the file is there. The seed
    reader wants a list, and `auction_rows` over a dict iterates its keys and builds
    nonsense from them. Without this the seed's candidate check is masked by the existence
    check and can be deleted green.
    """
    (quick_child / "listone_map.json").write_text(json.dumps({"uuid": {"fantacalcio_id": 7}}))

    response = TestClient(app).post(
        "/api/v1/harvest/backfill",
        json={"log": "live.jsonl", "seed": "listone_map.json"},
    )

    assert response.status_code == 400
    assert "listone_map.json" in response.json()["detail"]


def test_a_seed_path_that_escapes_the_home_is_refused_although_it_exists(
    quick_child,
) -> None:
    """The other half. A traversal in the seed field names a file that really is there,
    so an existence check passes it and only membership of the list refuses it."""
    escaped = quick_child.parent / "outside.json"
    escaped.write_text(json.dumps([["a", "15", 10, 500, 25, 25, "r", "f", 7, 7, "L"]]))

    response = TestClient(app).post(
        "/api/v1/harvest/backfill",
        json={"log": "live.jsonl", "seed": f"../{escaped.name}"},
    )

    assert response.status_code == 400
    assert "candidate seed" in response.json()["detail"]


def test_an_unknown_format_is_refused_with_the_commands_own_sentence(quick_child) -> None:
    """The same refusal the command makes, from the same function — not a second copy
    that agrees today."""
    response = TestClient(app).post(
        "/api/v1/harvest/backfill",
        json={"log": "live.jsonl", "seed": "seed.json", "asta_type": "manta"},
    )

    assert response.status_code == 400
    assert "manta" in response.json()["detail"]
    assert "mantra" in response.json()["detail"], "the legal values are named, as the CLI names them"
