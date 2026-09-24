"""`GET /db/scrape/tables` and `POST /db/scrape` (T23).

The longest write the app can start: three seasons of `voti` is ~114 polite GETs a
second apart, which is why it is a supervised child with a progress log rather than a
request. `endpoints/teams.py` states the other side of that line — a small GET plus an
insert answers in its own request — and this is the case it was contrasted with.

**The picker exists because of a stale default.** `voti.DEFAULT_SEASONS` and
`statistiche.DEFAULT_SEASONS` stopped at 2025/26 while 2026/27 was being played, so a
run that named no season scraped last season and reported success. Both reach 2026/27
now; the route is kept, because the next August puts them behind again, and one test
below shortens a scraper's list to keep the stale branch exercised. The form still
defaults to the season being played rather than inheriting the scraper's list.

**Seasons are sent explicitly, always.** `clean_scrape` resolves an empty list to the
scraper's own default before the child is spawned, so the argv in the job log is the
record of what ran — the one thing the terminal never said out loud.

No socket: `fantabot_command` is replaced with a short-lived child that prints its argv,
which is also where a route that grew its own `import_module` would stop being observable.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app

from .conftest import job as _job
from .conftest import redirect_home, stub_child_command
from .conftest import wait_for as _wait


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """A short-lived real child in place of the CLI, so argv is observable in the log."""
    stub_child_command(monkeypatch)
    redirect_home(monkeypatch, tmp_path)
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


def test_the_picker_flags_nothing_now_that_every_default_reaches_the_season(season_now) -> None:
    """Was `test_the_picker_flags_the_two_defaults_that_are_behind`, asserting `True` twice.

    The flags moved because the defaults did, not because the report went away: the route
    still computes staleness per table against the season being played, and the test below
    drives that path with a scraper put deliberately behind.
    """
    tables = {t["table"]: t for t in TestClient(app).get("/api/v1/db/scrape/tables").json()["tables"]}

    assert tables["voti"]["default_is_stale"] is False
    assert tables["statistiche"]["default_is_stale"] is False
    assert tables["quotazioni"]["default_is_stale"] is False


def test_the_picker_still_flags_a_default_that_falls_behind(season_now, monkeypatch) -> None:
    """The report is for the *next* August, so it is driven rather than left unexercised.

    With every shipped default current, the route's stale branch has no live input — and a
    branch no test reaches is one that can be broken without anything saying so. The
    scraper's own list is shortened here, which is also the assertion that the route reads
    that list rather than a copy.
    """
    from fantabot.adapters.scraping import voti

    monkeypatch.setattr(voti, "DEFAULT_SEASONS", list(voti.DEFAULT_SEASONS[:-1]))

    tables = {t["table"]: t for t in TestClient(app).get("/api/v1/db/scrape/tables").json()["tables"]}

    assert tables["voti"]["default_is_stale"] is True
    assert tables["statistiche"]["default_is_stale"] is False


def test_the_picker_carries_the_list_itself(season_now) -> None:
    """Not only whether it is behind — what it would take, so the operator can see it.

    This asserted `"2026/27" not in ...` while the default was short; the question it asks
    is unchanged and the answer moved.
    """
    tables = {t["table"]: t for t in TestClient(app).get("/api/v1/db/scrape/tables").json()["tables"]}

    assert "2026/27" in tables["voti"]["default_seasons"]
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


def test_the_child_is_spawned_with_the_cleaned_table_not_the_one_asked_for(
    quick_child, season_now
) -> None:
    """The mistake nothing downstream can detect: `clean_scrape` normalises the table, so
    a route that spawns `request.table` sends a name the command then refuses."""
    client = TestClient(app)

    job_id = client.post(
        "/api/v1/db/scrape", json={"table": " VOTI ", "seasons": ["2026/27"]}
    ).json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert "db scrape voti --season 2026/27" in " ".join(_job(client, job_id)["lines"])


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
