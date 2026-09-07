"""F2 — the Postgres provisioner, migration and chromium steps.

All faked: no real Postgres, no alembic, no network. The provisioner's job is
orchestration — start the server, ensure the `fantabot` database, export
`FANTABOT_DATABASE_URL` so fantabot's lazy engine finds it — and that logic is what
these pin. The real `pixeltable_pgserver` start is exercised by the opt-in integration
test and the cross-OS cold-install CI (SPEC A1), not here.
"""

from __future__ import annotations

import inspect

import pytest

from fantabot_app.provisioner.chromium import install_chromium
from fantabot_app.provisioner.migrate import upgrade_head
from fantabot_app.provisioner.postgres import (
    ENV_DATABASE_URL,
    PostgresProvisioner,
    _default_factory,
)


class FakeServer:
    """Stands in for a pixeltable_pgserver PostgresServer."""

    def __init__(self) -> None:
        self.stopped = False

    def get_uri(self, database: str | None = None, driver: str | None = None) -> str:
        db = database or "postgres"
        drv = f"+{driver}" if driver else ""
        return f"postgresql{drv}://postgres:@127.0.0.1:55555/{db}"

    def get_pid(self) -> int | None:
        return None if self.stopped else 4242

    def stop(self) -> None:
        self.stopped = True


def _provisioner(tmp_path, *, created=None, env=None):
    return PostgresProvisioner(
        pgdata=tmp_path / "pgdata",
        server_factory=lambda _pgdata: FakeServer(),
        create_db=lambda admin_uri, dbname: (created if created is not None else []).append(
            (admin_uri, dbname)
        ),
        environ=env if env is not None else {},
    )


def test_start_creates_db_exports_env_and_returns_fantabot_url(tmp_path) -> None:
    created: list[tuple[str, str]] = []
    env: dict[str, str] = {}
    prov = _provisioner(tmp_path, created=created, env=env)

    url = prov.start()

    assert url == "postgresql+psycopg2://postgres:@127.0.0.1:55555/fantabot"
    assert env[ENV_DATABASE_URL] == url
    # the fantabot db is created against the admin (postgres) database
    assert created == [("postgresql+psycopg2://postgres:@127.0.0.1:55555/postgres", "fantabot")]


def test_start_is_idempotent_and_reuses_one_server(tmp_path) -> None:
    starts: list = []
    prov = PostgresProvisioner(
        pgdata=tmp_path / "pgdata",
        server_factory=lambda pgdata: starts.append(pgdata) or FakeServer(),
        create_db=lambda admin_uri, dbname: None,
        environ={},
    )
    prov.start()
    prov.start()
    assert len(starts) == 1


def test_stop_then_status_reports_not_running(tmp_path) -> None:
    prov = _provisioner(tmp_path)
    prov.start()
    assert prov.status()["running"] is True
    prov.stop()
    assert prov.status()["running"] is False


def test_upgrade_head_runs_the_head_revision(tmp_path) -> None:
    calls: list[str] = []
    upgrade_head(run=lambda revision: calls.append(revision))
    assert calls == ["head"]


def test_install_chromium_invokes_playwright(tmp_path) -> None:
    commands: list[list[str]] = []
    install_chromium(run=lambda cmd: commands.append(cmd))
    assert commands and "playwright" in commands[0] and "chromium" in commands[0]


# --- an exported FANTABOT_DATABASE_URL wins (T2.1) ----------------------------------
#
# The split that made a week of Classic auction collection read as lost (root
# `CLAUDE.md`, "One database, and it is the app's") was this class overwriting the
# variable at launch. An export is an explicit instruction from the operator; a `.env`
# file found by whatever the working directory happens to be is not, and is deliberately
# NOT read here.

EXTERNAL = "postgresql+psycopg2://postgres:@/fantabot?host=%2FUsers%2Fx%2F.fantabot%2Fpgdata"


def _never_started(_pgdata):
    raise AssertionError("the server factory must not be called when the URL is exported")


def _external(tmp_path, *, created=None, env=None):
    return PostgresProvisioner(
        pgdata=tmp_path / "pgdata",
        server_factory=_never_started,
        create_db=lambda admin_uri, dbname: (created if created is not None else []).append(
            (admin_uri, dbname)
        ),
        environ={ENV_DATABASE_URL: EXTERNAL} if env is None else env,
    )


def test_start_returns_an_exported_url_and_starts_no_server(tmp_path) -> None:
    created: list[tuple[str, str]] = []
    env = {ENV_DATABASE_URL: EXTERNAL}
    prov = _external(tmp_path, created=created, env=env)

    assert prov.start() == EXTERNAL
    assert env[ENV_DATABASE_URL] == EXTERNAL  # never overwritten
    assert created == []  # not our server; not ours to create a database on
    assert not (tmp_path / "pgdata").exists()


def test_database_url_reports_the_exported_url_without_start(tmp_path) -> None:
    assert _external(tmp_path).database_url() == EXTERNAL


def test_status_reports_the_exported_url_and_an_unknown_running_state(tmp_path) -> None:
    assert _external(tmp_path).status() == {
        "running": None,
        "pid": None,
        "url": EXTERNAL,
        "external": True,
        "provisioned": False,
        "pgdata": str(tmp_path / "pgdata"),
    }


def test_stop_is_a_no_op_against_an_external_server(tmp_path) -> None:
    prov = _external(tmp_path)
    prov.start()
    prov.stop()
    assert prov.database_url() == EXTERNAL


def test_a_blank_exported_url_falls_through_to_the_bundled_server(tmp_path) -> None:
    env = {ENV_DATABASE_URL: "   "}
    prov = _provisioner(tmp_path, env=env)

    url = prov.start()

    assert url == "postgresql+psycopg2://postgres:@127.0.0.1:55555/fantabot"
    assert env[ENV_DATABASE_URL] == url


def test_the_bundled_server_is_reported_as_not_external(tmp_path) -> None:
    prov = _provisioner(tmp_path)
    prov.start()
    assert prov.status() == {
        "running": True,
        "pid": 4242,
        "url": "postgresql+psycopg2://postgres:@127.0.0.1:55555/fantabot",
        "external": False,
        "provisioned": False,
        "pgdata": str(tmp_path / "pgdata"),
    }


# --- the bundled server outlives the process that started it (T2.2) ---------------
#
# `db start` must leave Postgres up, and `db stop` must stop a server THIS process never
# started. Neither is reachable through `_server`, which `start()` sets only in its own
# process, so the provisioner grew a pid-file reader and a stopper — both injected, so
# these tests open no socket and start no server.


class FakePostmaster:
    """What a running `postmaster.pid` says (mirrors pgserver's PostmasterInfo)."""

    def __init__(self, *, pid: int = 30763, socket_dir: str = "/tmp/pg") -> None:
        self.pid = pid
        self._socket_dir = socket_dir

    def is_running(self) -> bool:
        return True

    def get_uri(
        self,
        user: str = "postgres",
        database: str | None = None,
        driver: str | None = None,
    ) -> str:
        drv = f"+{driver}" if driver else ""
        return f"postgresql{drv}://{user}:@/{database}?host={self._socket_dir}"


def _provisioned(tmp_path):
    """A pgdata that `initdb` has already run in — PG_VERSION is the marker pgserver uses."""
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir(parents=True, exist_ok=True)
    (pgdata / "PG_VERSION").write_text("18\n")
    return pgdata


def _fresh(tmp_path, *, postmaster=None, stopped=None, created=None, env=None):
    """A provisioner in a process that never called start() — the `db stop` case."""
    return PostgresProvisioner(
        pgdata=tmp_path / "pgdata",
        server_factory=_never_started,
        create_db=lambda admin_uri, dbname: (created if created is not None else []).append(
            (admin_uri, dbname)
        ),
        postmaster=postmaster if postmaster is not None else (lambda _pgdata: None),
        stop_server=lambda pgdata: (stopped if stopped is not None else []).append(pgdata),
        environ=env if env is not None else {},
    )


def test_the_default_factory_never_lets_the_server_die_with_the_process() -> None:
    """cleanup_mode=None: pgserver's atexit hook stops the server at 'stop' (its default).

    No fake can observe this — the provisioner passes nothing and takes the default — so
    the default itself is the assertion.
    """
    assert inspect.signature(_default_factory).parameters["cleanup_mode"].default is None


def test_stop_bundled_on_an_unprovisioned_pgdata_touches_nothing(tmp_path) -> None:
    """Never initdb in order to stop: get_server(pgdata) would create a whole cluster."""
    stopped: list = []
    reads: list = []
    prov = _fresh(
        tmp_path,
        postmaster=lambda pgdata: reads.append(pgdata),  # returns None
        stopped=stopped,
    )

    assert prov.stop_bundled() == "not_provisioned"
    assert stopped == []
    assert reads == []  # not even the pid file is read


def test_stop_bundled_stops_a_server_this_process_never_started(tmp_path) -> None:
    pgdata = _provisioned(tmp_path)
    stopped: list = []
    prov = _fresh(tmp_path, postmaster=lambda _p: FakePostmaster(), stopped=stopped)

    assert prov.stop_bundled() == "stopped"
    assert stopped == [pgdata]


def test_stop_bundled_reports_a_server_that_is_already_down(tmp_path) -> None:
    _provisioned(tmp_path)
    stopped: list = []
    prov = _fresh(tmp_path, postmaster=lambda _p: None, stopped=stopped)

    assert prov.stop_bundled() == "not_running"
    assert stopped == []


def test_stop_bundled_ignores_an_exported_url(tmp_path) -> None:
    """`db start` prints an `export` line the operator pastes; `db stop` must still stop.

    `stop()` keeps T2.1's external contract — it manages only what it started.
    `stop_bundled()` addresses the pgdata directory, which is what the CLI asks for.
    """
    pgdata = _provisioned(tmp_path)
    stopped: list = []
    prov = _fresh(
        tmp_path,
        postmaster=lambda _p: FakePostmaster(),
        stopped=stopped,
        env={ENV_DATABASE_URL: EXTERNAL},
    )

    assert prov.stop_bundled() == "stopped"
    assert stopped == [pgdata]
    prov.stop()  # the T2.1 contract, unchanged
    assert stopped == [pgdata]


def test_status_reports_a_running_server_this_process_never_started(tmp_path) -> None:
    _provisioned(tmp_path)
    prov = _fresh(tmp_path, postmaster=lambda _p: FakePostmaster(pid=30763))

    assert prov.status() == {
        "running": True,
        "pid": 30763,
        "url": "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg",
        "external": False,
        "provisioned": True,
        "pgdata": str(tmp_path / "pgdata"),
    }


def test_status_separates_provisioned_but_stopped_from_never_provisioned(tmp_path) -> None:
    down = _fresh(tmp_path)
    assert down.status()["provisioned"] is False
    assert down.status()["running"] is False
    assert down.status()["url"] is None

    _provisioned(tmp_path)
    assert down.status()["provisioned"] is True
    assert down.status()["running"] is False


def test_bundled_url_names_another_database(tmp_path) -> None:
    """`db url --database fantabot_test` is how the db tier gets its DSN (T2.6)."""
    _provisioned(tmp_path)
    prov = _fresh(tmp_path, postmaster=lambda _p: FakePostmaster())

    assert prov.bundled_url() == "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg"
    assert prov.bundled_url(database="fantabot_test") == (
        "postgresql+psycopg2://postgres:@/fantabot_test?host=/tmp/pg"
    )


def test_bundled_url_is_none_when_the_server_is_down(tmp_path) -> None:
    """The socket dir is only knowable from postmaster.pid, so a stopped server has no
    DSN to print — pgserver may pick a hashed dir under TMPDIR. Refuse rather than guess.
    """
    _provisioned(tmp_path)
    assert _fresh(tmp_path).bundled_url() is None


def test_create_database_refuses_a_name_that_is_not_an_identifier(tmp_path) -> None:
    """The name reaches an f-string in a CREATE DATABASE on a superuser connection."""
    _provisioned(tmp_path)
    created: list = []
    prov = _fresh(tmp_path, postmaster=lambda _p: FakePostmaster(), created=created)

    with pytest.raises(ValueError):
        prov.create_database('x"; DROP DATABASE fantabot; --')
    assert created == []


def test_create_database_reaches_the_admin_database_from_the_pid_file(tmp_path) -> None:
    _provisioned(tmp_path)
    created: list = []
    prov = _fresh(tmp_path, postmaster=lambda _p: FakePostmaster(), created=created)

    prov.create_database("fantabot_test")

    assert created == [
        ("postgresql+psycopg2://postgres:@/postgres?host=/tmp/pg", "fantabot_test")
    ]
