"""`GET /db/scrape/tables` and `POST /db/scrape` (T23).

The longest write the app can start: three seasons of `voti` is ~114 polite GETs a
second apart, which is why it is a supervised child with a progress log rather than a
request. `endpoints/teams.py` states the other side of that line — a small GET plus an
insert answers in its own request — and this is the case it was contrasted with.

**The picker exists because of a stale default.** `voti.DEFAULT_SEASONS` and
`statistiche.DEFAULT_SEASONS` stop at 2025/26 while 2026/27 is being played, so a run
that names no season scrapes last season and reports success. The route reports which
defaults are stale and against which season, and the form defaults to the season being
played rather than inheriting the scraper's list.

**Seasons are sent explicitly, always.** `clean_scrape` resolves an empty list to the
scraper's own default before the child is spawned, so the argv in the job log is the
record of what ran — the one thing the terminal never said out loud.

No socket: `fantabot_command` is replaced with a short-lived child that prints its argv,
which is also where a route that grew its own `import_module` would stop being observable.
"""

from __future__ import annotations

import sys
import time
from datetime import date

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


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """A short-lived real child in place of the CLI, so argv is observable in the log."""
    from fantabot_app.api.infrastructure import processes

    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def season_now(monkeypatch):
    """Freeze the season being played. Every assertion below would move each August."""
    from fantabot_app.api.v1.endpoints import scrape

    monkeypatch.setattr(scrape, "_today", lambda: date(2026, 9, 20))


# -- GET /db/scrape/tables --------------------------------------------------------------


def test_the_picker_lists_the_three_in_the_order_a_fresh_database_needs(season_now) -> None:
    body = TestClient(app).get("/api/v1/db/scrape/tables").json()

    assert [t["table"] for t in body["tables"]] == ["quotazioni", "statistiche", "voti"]
    assert body["tables"][0]["requires"] == []
    assert body["tables"][2]["requires"] == ["quotazioni"]


def test_each_table_says_which_rows_it_writes(season_now) -> None:
    tables = {t["table"]: t for t in TestClient(app).get("/api/v1/db/scrape/tables").json()["tables"]}

    assert tables["quotazioni"]["writes"] == ["quotazioni", "players", "teams"]
    assert tables["voti"]["writes"] == ["voti", "bonus_malus"]


def test_the_picker_names_the_season_being_played(season_now) -> None:
    assert TestClient(app).get("/api/v1/db/scrape/tables").json()["current_season"] == "2026/27"


def test_the_picker_flags_the_two_defaults_that_are_behind(season_now) -> None:
    """The trap, carried onto the screen: those two would scrape last season silently."""
    tables = {t["table"]: t for t in TestClient(app).get("/api/v1/db/scrape/tables").json()["tables"]}

    assert tables["voti"]["default_is_stale"] is True
    assert tables["statistiche"]["default_is_stale"] is True
    assert tables["quotazioni"]["default_is_stale"] is False


def test_the_picker_carries_the_stale_list_itself(season_now) -> None:
    """Not only that it is behind — what it would have taken, so the operator can see it."""
    tables = {t["table"]: t for t in TestClient(app).get("/api/v1/db/scrape/tables").json()["tables"]}

    assert "2026/27" not in tables["voti"]["default_seasons"]
    assert "2025/26" in tables["voti"]["default_seasons"]


def test_the_calendar_is_read_in_one_place(monkeypatch) -> None:
    """The seam, asserted rather than assumed: a second read is a second thing to freeze."""
    from fantabot_app.api.v1.endpoints import scrape

    monkeypatch.setattr(scrape, "_today", lambda: date(2023, 3, 1))

    body = TestClient(app).get("/api/v1/db/scrape/tables").json()

    assert body["current_season"] == "2022/23"
    assert {t["table"]: t["default_is_stale"] for t in body["tables"]}["voti"] is False


# -- POST /db/scrape --------------------------------------------------------------------


def test_a_run_spawns_the_command_with_every_season_named(quick_child, season_now) -> None:
    client = TestClient(app)

    job_id = client.post(
        "/api/v1/db/scrape", json={"table": "voti", "seasons": ["2026/27", "2025/26"]}
    ).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    log = " ".join(_job(client, job_id)["lines"])
    assert "db scrape voti" in log
    assert "--season 2026/27" in log
    assert "--season 2025/26" in log


def test_no_seasons_is_resolved_before_the_child_is_spawned(quick_child, season_now) -> None:
    """Never a bare `db scrape voti`: the argv in the log is the record of what ran."""
    from fantabot.adapters.scraping import voti

    client = TestClient(app)
    job_id = client.post("/api/v1/db/scrape", json={"table": "voti"}).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    log = " ".join(_job(client, job_id)["lines"])
    for season in voti.DEFAULT_SEASONS:
        assert f"--season {season}" in log


def test_the_job_says_what_kind_it_is(quick_child, season_now) -> None:
    client = TestClient(app)
    job_id = client.post(
        "/api/v1/db/scrape", json={"table": "quotazioni", "seasons": ["2026/27"]}
    ).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    kinds = {job["id"]: job["kind"] for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert kinds[job_id] == "db-scrape"


def test_the_job_is_stoppable(quick_child, season_now) -> None:
    """Minutes long, and a scrape is re-run rather than repaired: every write is an
    upsert and `voti` commits per giornata, so a stop costs fetch time and no rows."""
    client = TestClient(app)
    job_id = client.post(
        "/api/v1/db/scrape", json={"table": "voti", "seasons": ["2026/27"]}
    ).json()["job_id"]

    listed = {job["id"]: job for job in client.get("/api/v1/jobs").json()["jobs"]}
    assert listed[job_id]["stoppable"] is True


def test_two_tables_do_not_share_a_stop_flag(quick_child) -> None:
    """A stop aimed at a `voti` run must not end a `quotazioni` one — `stop_path`'s rule."""
    from fantabot_app.api.v1.endpoints.scrape import scrape_flag

    assert scrape_flag("voti") != scrape_flag("quotazioni")


# -- the refusals, which are the command's ----------------------------------------------


def test_an_unknown_table_is_refused_and_nothing_is_spawned(quick_child, season_now) -> None:
    client = TestClient(app)
    before = len(client.get("/api/v1/jobs").json()["jobs"])

    response = client.post("/api/v1/db/scrape", json={"table": "fixtures"})

    assert response.status_code == 400
    assert "quotazioni" in response.json()["detail"]
    assert len(client.get("/api/v1/jobs").json()["jobs"]) == before


def test_a_malformed_season_is_refused_with_the_commands_own_sentence(
    quick_child, season_now
) -> None:
    response = TestClient(app).post(
        "/api/v1/db/scrape", json={"table": "voti", "seasons": ["2022/26"]}
    )

    assert response.status_code == 400
    assert "2022/26" in response.json()["detail"]
