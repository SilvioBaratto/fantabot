"""The corpus panel's endpoint — what is stored, per format.

The instrument every later collection increment is graded on (`todo/TODO.md` §2, which
says to build it first, and §1.1 for what an unfalsifiable "collection worked" cost).
Read-only, and it degrades open like `/db/health`: this is the endpoint that reports a
down database, so it must not be the endpoint that 500s when the database is down.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.harvest import read_corpus


class FakeAsteRepo:
    def __init__(self, rows: list[object] | None = None, *, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises

    def corpus_summary(self, *, num_credits: int = 500, num_teams: int = 8) -> list[object]:
        if self._raises:
            raise RuntimeError("db unreachable")
        return self._rows


class Row:
    """A `CorpusRow` stand-in — the endpoint reads attributes, not a class."""

    def __init__(self, asta_type: str, **counts: int) -> None:
        self.asta_type = asta_type
        self.rooms = counts.get("rooms", 0)
        self.rooms_with_events = counts.get("rooms_with_events", 0)
        self.events = counts.get("events", 0)
        self.assignments = counts.get("assignments", 0)
        self.assignments_with_buyer = counts.get("assignments_with_buyer", 0)
        self.assignments_with_player = counts.get("assignments_with_player", 0)
        self.planner_sales = counts.get("planner_sales", 0)


def test_read_corpus_carries_every_count_through() -> None:
    corpus = read_corpus(
        FakeAsteRepo([Row("classic", rooms=4386, events=2145179, planner_sales=32100)])
    )

    assert corpus.ok is True
    assert [row.asta_type for row in corpus.formats] == ["classic"]
    assert corpus.formats[0].events == 2145179
    assert corpus.formats[0].planner_sales == 32100


def test_read_corpus_states_the_filter_the_headline_number_survived() -> None:
    """The number is meaningless without it, and a tooltip is not the panel's contract.

    `planner_sales` is `clearing_sales` under `8 x 500` with a buyer and a player link;
    read against another shape it is a different number entirely, so the shape travels
    with the answer rather than being assumed by whoever renders it.
    """
    corpus = read_corpus(FakeAsteRepo([]), num_credits=250, num_teams=10)

    assert corpus.num_credits == 250
    assert corpus.num_teams == 10


def test_read_corpus_degrades_open_when_the_repository_raises() -> None:
    corpus = read_corpus(FakeAsteRepo(raises=True))

    assert corpus.ok is False
    assert corpus.formats == []
    assert corpus.error


def test_the_endpoint_never_500s_when_the_database_is_down(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/harvest/corpus")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["formats"] == []


# ---------------------------------------------------------------------------------------
# The seed panel — what the registry holds, and what a scan just added to it.
# ---------------------------------------------------------------------------------------


def _write_seed(path, rows) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


def test_read_seed_splits_the_registry_by_format(tmp_path) -> None:
    from fantabot_app.api.v1.endpoints.harvest import read_seed

    seed = tmp_path / "seed.json"
    _write_seed(
        seed,
        [
            ["a1", "1", 8, 500, 2, 28, "m", "r", 30, 60, "Alpha", "classic"],
            ["a2", "1", 8, 500, 2, 28, "m", "r", 30, 60, "Beta", "mantra"],
            ["a3", "2", 8, 500, 2, 28, "m", "r", 30, 60, "Gamma", "classic"],
        ],
    )

    panel = read_seed(seed)

    assert panel.ok is True
    assert panel.exists is True
    assert panel.rows == 3
    assert panel.formats == {"classic": 2, "mantra": 1}
    assert panel.path == str(seed)
    assert panel.mtime


def test_read_seed_calls_a_poller_era_row_mantra(tmp_path) -> None:
    """The eleven-field rows predate storing the format, and every one of them was Mantra.

    The same fallback `from_seed_row` applies, and for the same reason: reading them as
    anything else is how 185 Classic auctions came to be labelled Mantra.
    """
    from fantabot_app.api.v1.endpoints.harvest import read_seed

    seed = tmp_path / "seed.json"
    _write_seed(seed, [["a1", "1", 8, 500, 2, 28, "m", "r", 30, 60, "Alpha"]])

    panel = read_seed(seed)

    assert panel.formats == {"mantra": 1}


def test_read_seed_reports_a_missing_file_rather_than_zero_rows(tmp_path) -> None:
    """Zero rows and no file are different answers, and only one names a command."""
    from fantabot_app.api.v1.endpoints.harvest import read_seed

    panel = read_seed(tmp_path / "nothing.json")

    assert panel.ok is True
    assert panel.exists is False
    assert panel.rows == 0
    assert panel.mtime is None


def test_read_seed_degrades_open_on_a_torn_file(tmp_path) -> None:
    from fantabot_app.api.v1.endpoints.harvest import read_seed

    seed = tmp_path / "seed.json"
    seed.write_text("[[1, 2", encoding="utf-8")

    panel = read_seed(seed)

    assert panel.ok is False
    assert panel.error
    assert panel.rows == 0


def test_the_seed_endpoint_reads_the_harvest_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(tmp_path))
    _write_seed(
        tmp_path / "seed.json",
        [["a1", "1", 8, 500, 2, 28, "m", "r", 30, 60, "Alpha", "classic"]],
    )

    body = TestClient(app).get("/api/v1/harvest/seed").json()

    assert body["ok"] is True
    assert body["rows"] == 1
    assert body["formats"] == {"classic": 1}
    assert body["path"] == str(tmp_path / "seed.json")


# ---------------------------------------------------------------------------------------
# POST /actions/harvest-scan — one authenticated GET, on the ordinary job idiom.
# ---------------------------------------------------------------------------------------

def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _wait(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _config(auction_id: str, asta_type: str):
    from fantabot.domain.harvest.registry import AuctionConfig

    return AuctionConfig(auction_id=auction_id, db_shard="1", asta_type=asta_type)


@pytest.fixture
def scan_home(monkeypatch, tmp_path):
    """A harvest home, an open session and a client that answers whatever is set on it."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens import fantalab_store as store_mod
    from fantabot.domain.tokens import crypto

    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(tmp_path))

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(database_manager, "get_session", fake_session)
    monkeypatch.setattr(crypto, "TokenCipher", lambda key: None)
    monkeypatch.setattr(store_mod, "FantalabStore", lambda session, cipher: None)
    return tmp_path


def _answer_scan(monkeypatch, answer):
    """Make `LiveAuctionsClient.from_store(...).live_auctions()` do `answer()`."""
    from fantabot.adapters.http.harvest import client as client_mod

    class _Client:
        def live_auctions(self):
            return answer()

    monkeypatch.setattr(client_mod.LiveAuctionsClient, "from_store", staticmethod(lambda store: _Client()))


def test_harvest_scan_merges_into_the_home_seed_and_reports_the_split(
    monkeypatch, scan_home
) -> None:
    _write_seed(
        scan_home / "seed.json",
        [["a1", "1", 8, 500, 2, 28, "m", "r", 30, 60, "Alpha", "classic"]],
    )
    _answer_scan(monkeypatch, lambda: [_config("a1", "classic"), _config("a2", "mantra")])

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/harvest-scan").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    job = _job(client, job_id)
    assert job["ok"] is True
    assert any("registry 1 -> 2 (+1)" in line for line in job["lines"]), job["lines"]
    assert any("classic 1" in line and "mantra 1" in line for line in job["lines"])

    panel = client.get("/api/v1/harvest/seed").json()
    assert panel["rows"] == 2
    assert panel["formats"] == {"classic": 1, "mantra": 1}


def test_harvest_scan_writes_a_seed_that_did_not_exist(monkeypatch, scan_home) -> None:
    _answer_scan(monkeypatch, lambda: [_config("a1", "mantra")])

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/harvest-scan").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert (scan_home / "seed.json").exists()


def test_auth_expired_fails_the_job_with_its_own_message(monkeypatch, scan_home) -> None:
    """A refusal, not an empty result: reporting zero would look like a quiet night."""
    from fantabot.adapters.http.harvest.client import AuthExpired

    def boom():
        raise AuthExpired("the stored FantaLab session no longer authenticates")

    _answer_scan(monkeypatch, boom)

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/harvest-scan").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "error")
    assert "no longer authenticates" in _job(client, job_id)["error"]


def test_scan_empty_fails_the_job_with_its_own_message(monkeypatch, scan_home) -> None:
    from fantabot.adapters.http.harvest.client import ScanEmpty

    def boom():
        raise ScanEmpty("nothing is live right now")

    _answer_scan(monkeypatch, boom)

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/harvest-scan").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "error")
    assert "nothing is live" in _job(client, job_id)["error"]


def test_a_scan_with_no_stored_session_fails_the_job_not_the_trigger(
    monkeypatch, scan_home
) -> None:
    from fantabot.adapters.http.harvest import client as client_mod
    from fantabot.domain.tokens.errors import FantalabSessionMissing

    def boom(store):
        raise FantalabSessionMissing()

    monkeypatch.setattr(client_mod.LiveAuctionsClient, "from_store", staticmethod(boom))

    client = TestClient(app)
    response = client.post("/api/v1/actions/harvest-scan")

    assert response.status_code == 200  # the trigger never 500s; the job carries the failure
    job_id = response.json()["job_id"]
    assert _wait(lambda: _job(client, job_id)["status"] == "error")
    assert "fantalab-login" in _job(client, job_id)["error"]


def test_no_format_filter_is_reachable_from_the_app() -> None:
    """`--only` is not exposed, in the endpoint or anywhere near it.

    Filtering is a query, never a decision taken at collection time: the poller
    filtering to Mantra is what threw away 85% of the population.
    """
    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/actions/harvest-scan"]["post"]

    assert operation.get("parameters", []) == []


# ---------------------------------------------------------------------------------------
# POST /harvest/load — the supervisor, proven on the idempotent command first.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def quick_child(monkeypatch, tmp_path):
    """Point the supervisor at a short-lived real child instead of the fantabot CLI.

    A real child, deliberately: the whole property being tested is what the operating
    system does with a pipe and a signal, and a fake `Popen` would agree with whatever
    the implementation happened to do.
    """
    import sys

    from fantabot_app.api.infrastructure import processes

    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(tmp_path))
    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r}, flush=True)"],
    )
    return tmp_path


def test_a_supervised_load_shows_up_in_the_job_list_and_can_be_stopped(quick_child) -> None:
    client = TestClient(app)

    job_id = client.post("/api/v1/harvest/load?asta_type=classic").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    row = next(row for row in client.get("/api/v1/jobs").json()["jobs"] if row["id"] == job_id)
    assert row["kind"] == "harvest-load"
    # The whole reason T7 could land before this one: a thread job answers 409 here, and
    # a supervised process is the first kind that can honestly answer yes.
    assert row["stoppable"] is True


def test_the_supervised_command_carries_the_format_and_follow(quick_child) -> None:
    """`--asta-type` is not the filter the scan is forbidden.

    A load reads one format's auctions out of a seed that holds both, so the format is a
    parameter of *this* read. What the app must never own is a collection-time filter —
    the thing that decides which auctions are ever heard from.
    """
    client = TestClient(app)

    job_id = client.post("/api/v1/harvest/load?asta_type=classic&follow=true").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    log = " ".join(_job(client, job_id)["lines"])
    assert "harvest load --asta-type classic --follow" in log


def test_an_unknown_format_is_refused_before_anything_is_spawned(quick_child) -> None:
    response = TestClient(app).post("/api/v1/harvest/load?asta_type=serie-b")

    assert response.status_code == 400
    assert "serie-b" in response.json()["detail"]


def test_a_second_load_fails_the_job_naming_the_role_and_the_landing_zone(
    quick_child,
) -> None:
    """Killing the app leaves the child running; the next start must find it through the
    OS. Here the lock stands in for that child."""
    from fantabot.adapters.files.lock import role_lock

    landing = quick_child / "live.jsonl"
    client = TestClient(app)

    with role_lock(landing, "loader"):
        job_id = client.post("/api/v1/harvest/load").json()["job_id"]
        assert _wait(lambda: _job(client, job_id)["status"] == "error")

    error = _job(client, job_id)["error"]
    assert "loader" in error
    assert str(landing) in error


# ---------------------------------------------------------------------------------------
# POST /harvest/collect — the irreplaceable one, on the supervisor the loader proved.
# ---------------------------------------------------------------------------------------


def _seed_of(home, count: int, asta_type: str = "mantra") -> None:
    _write_seed(
        home / "seed.json",
        [
            [f"a{i}", "1", 8, 500, 2, 28, "m", "r", 30, 60, f"Room {i}", asta_type]
            for i in range(count)
        ],
    )


def test_a_pool_below_the_population_refuses_before_anything_is_spawned(quick_child) -> None:
    """Silent starvation, and the number is measured: a watcher on a live evening does not
    finish, so a queued auction never gets a permit and never connects at all. That cost
    145 of 395 auctions on 2026-08-27."""
    _seed_of(quick_child, 1705)

    response = TestClient(app).post("/api/v1/harvest/collect?pool=1000")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "1000" in detail and "1705" in detail, detail
    assert "pool" in detail


def test_a_pool_at_the_population_is_accepted(quick_child) -> None:
    _seed_of(quick_child, 4)
    client = TestClient(app)

    job_id = client.post("/api/v1/harvest/collect?pool=4").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert "harvest collect --pool 4" in " ".join(_job(client, job_id)["lines"])


def test_a_missing_seed_is_refused_by_name_rather_than_followed_as_nothing(
    quick_child,
) -> None:
    """A collect with an empty registry is a three-hour run that follows no auction, and
    it looks exactly like a quiet night from the outside."""
    response = TestClient(app).post("/api/v1/harvest/collect?pool=1000")

    assert response.status_code == 400
    assert "scan" in response.json()["detail"].lower()


def test_no_format_selector_exists_in_the_endpoint(quick_child) -> None:
    """`from_seed_row` reads each row's own `asta_type`, so one seed carries both.

    A selector here would be the collection-time filter again, wearing the name of a
    convenience.
    """
    schema = TestClient(app).get("/openapi.json").json()
    names = [
        p["name"] for p in schema["paths"]["/api/v1/harvest/collect"]["post"].get("parameters", [])
    ]

    assert names == ["pool"]


def test_the_heartbeat_reaches_the_job_log(monkeypatch, quick_child) -> None:
    """`live / expected` is the only thing that speaks during a run with no end."""
    import sys

    from fantabot_app.api.infrastructure import processes

    _seed_of(quick_child, 2)
    monkeypatch.setattr(
        processes,
        "fantabot_command",
        lambda *args: [sys.executable, "-c", "print('live 2 / expected 2', flush=True)"],
    )
    client = TestClient(app)

    job_id = client.post("/api/v1/harvest/collect?pool=2").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert "live 2 / expected 2" in _job(client, job_id)["lines"]


def test_a_second_collect_fails_the_job_naming_the_collector_role(quick_child) -> None:
    from fantabot.adapters.files.lock import role_lock

    _seed_of(quick_child, 2)
    landing = quick_child / "live.jsonl"
    client = TestClient(app)

    with role_lock(landing, "collector"):
        job_id = client.post("/api/v1/harvest/collect?pool=2").json()["job_id"]
        assert _wait(lambda: _job(client, job_id)["status"] == "error")

    error = _job(client, job_id)["error"]
    assert "collector" in error
    assert str(landing) in error


def test_the_collect_job_is_stoppable(quick_child) -> None:
    _seed_of(quick_child, 2)
    client = TestClient(app)

    job_id = client.post("/api/v1/harvest/collect?pool=2").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    row = next(r for r in client.get("/api/v1/jobs").json()["jobs"] if r["id"] == job_id)
    assert row["kind"] == "harvest-collect"
    assert row["stoppable"] is True


def test_the_seed_panel_carries_the_pool_the_next_collect_would_use(quick_child) -> None:
    """So the UI does not have to hardcode 1000 beside a constant that moved once already."""
    from fantabot.application.harvest_supervisor import DEFAULT_POOL

    _seed_of(quick_child, 3)

    panel = TestClient(app).get("/api/v1/harvest/seed").json()

    assert panel["default_pool"] == DEFAULT_POOL
