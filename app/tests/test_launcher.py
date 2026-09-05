"""F1 — the launcher CLI, its `db` group, and the per-user paths.

These are small tests: no I/O, no Postgres, no network. They pin the public shape of
`fantabot_app` — the command set, the `db` group's stdout/stderr/exit-code contract, and
paths under a per-user `~/.fantabot` home.

The provisioner is reached through the `cli._provisioner` seam, so every case here runs a
**real** `PostgresProvisioner` with a fake pid-file reader and a fake stopper: the logic
under test is the CLI's, and no server is started to test it.
"""

from contextlib import contextmanager

import click
import pytest
import typer
from typer.testing import CliRunner

from fantabot_app import cli, paths
from fantabot_app.cli import app
from fantabot_app.provisioner.postgres import ENV_DATABASE_URL, PostgresProvisioner

runner = CliRunner()

DSN = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"


class FakePostmaster:
    def __init__(self, *, pid: int = 30763) -> None:
        self.pid = pid

    def is_running(self) -> bool:
        return True

    def get_uri(self, user="postgres", database=None, driver=None) -> str:
        drv = f"+{driver}" if driver else ""
        return f"postgresql{drv}://{user}:@/{database}?host=/Users/x/.fantabot/pgdata"


def _install(monkeypatch, tmp_path, *, running=True, provisioned=True, env=None):
    """Point the CLI at a provisioner whose Postgres is entirely fake."""
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir(parents=True, exist_ok=True)
    if provisioned:
        (pgdata / "PG_VERSION").write_text("18\n")
    stopped: list = []
    created: list = []
    prov = PostgresProvisioner(
        pgdata=pgdata,
        server_factory=lambda _p: (_ for _ in ()).throw(AssertionError("started a server")),
        create_db=lambda admin_uri, dbname: created.append((admin_uri, dbname)),
        postmaster=lambda _p: FakePostmaster() if running else None,
        stop_server=lambda path: stopped.append(path),
        environ={} if env is None else env,
    )
    monkeypatch.setattr(cli, "_provisioner", lambda: prov)
    return prov, stopped, created


def _leaves(command: click.Command, prefix: tuple[str, ...] = ()) -> set[str]:
    """Every runnable command, as the user types it (ported from the fantabot CLI's own)."""
    subcommands = getattr(command, "commands", None)
    if not subcommands:
        return {" ".join(prefix)}
    return {leaf for name, sub in subcommands.items() for leaf in _leaves(sub, (*prefix, name))}


def test_the_command_set_is_exactly_what_is_declared() -> None:
    """A `--help` substring check passed "up" against the word "setup"; walk the tree."""
    assert _leaves(typer.main.get_command(app)) == {
        "setup",
        "up",
        "stop",
        "doctor",
        "db start",
        "db stop",
        "db status",
        "db url",
        "db create",
        "harvest adopt",
    }


def test_help_exits_clean(monkeypatch) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


# --- `db url` is a return value, not a message ---------------------------------------
#
# It is consumed as FANTABOT_DATABASE_URL="$(fantabot-app db url --database fantabot_test)",
# so stdout carries the DSN and nothing else, and a missing DSN is a nonzero exit rather
# than an empty string that reads as success.


def test_db_url_prints_the_dsn_alone_on_stdout(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)
    result = runner.invoke(app, ["db", "url"])
    assert result.exit_code == 0
    assert result.stdout.strip() == DSN
    assert result.stderr == ""


def test_db_url_prints_the_socket_path_unencoded(monkeypatch, tmp_path) -> None:
    """alembic's ConfigParser interpolates `%`; a percent-encoded socket path breaks it."""
    _install(monkeypatch, tmp_path)
    assert "%2F" not in runner.invoke(app, ["db", "url"]).stdout


def test_db_url_on_a_stopped_server_says_so_on_stderr_and_exits_2(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path, running=False)
    result = runner.invoke(app, ["db", "url"])
    assert result.exit_code == 2
    assert result.stdout.strip() == ""
    assert "db start" in result.stderr


def test_db_url_names_another_database(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)
    result = runner.invoke(app, ["db", "url", "--database", "fantabot_test"])
    assert result.stdout.strip().endswith("/fantabot_test?host=/Users/x/.fantabot/pgdata")


def test_db_url_returns_an_exported_url_verbatim(monkeypatch, tmp_path) -> None:
    external = "postgresql+psycopg2://postgres:secret@localhost:5433/other"
    _install(monkeypatch, tmp_path, env={ENV_DATABASE_URL: external})
    result = runner.invoke(app, ["db", "url"])
    assert result.exit_code == 0
    assert result.stdout.strip() == external  # a password is not redacted out of a DSN


def test_db_url_with_a_database_addresses_the_bundled_server_despite_an_export(
    monkeypatch, tmp_path
) -> None:
    """Answering `--database fantabot_test` with the canonical URL would point the whole
    `-m db` tier at the real 614k-row database (T2.6)."""
    external = "postgresql+psycopg2://postgres:@/fantabot?host=/elsewhere"
    _install(monkeypatch, tmp_path, env={ENV_DATABASE_URL: external})
    result = runner.invoke(app, ["db", "url", "--database", "fantabot_test"])
    assert result.stdout.strip().endswith("/fantabot_test?host=/Users/x/.fantabot/pgdata")
    assert "FANTABOT_DATABASE_URL" in result.stderr


# --- stop actually stops -------------------------------------------------------------


def test_db_stop_stops_a_server_this_process_never_started(monkeypatch, tmp_path) -> None:
    prov, stopped, _ = _install(monkeypatch, tmp_path)
    result = runner.invoke(app, ["db", "stop"])
    assert result.exit_code == 0
    assert stopped == [prov.status()["pgdata"] and tmp_path / "pgdata"]


def test_db_stop_on_a_stopped_server_is_not_an_error(monkeypatch, tmp_path) -> None:
    _, stopped, _ = _install(monkeypatch, tmp_path, running=False)
    result = runner.invoke(app, ["db", "stop"])
    assert result.exit_code == 0
    assert stopped == []


def test_db_stop_still_stops_when_a_url_is_exported(monkeypatch, tmp_path) -> None:
    _, stopped, _ = _install(monkeypatch, tmp_path, env={ENV_DATABASE_URL: DSN})
    result = runner.invoke(app, ["db", "stop"])
    assert result.exit_code == 0
    assert stopped == [tmp_path / "pgdata"]


def test_the_top_level_stop_delegates_to_db_stop(monkeypatch, tmp_path) -> None:
    _, stopped, _ = _install(monkeypatch, tmp_path)
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert stopped == [tmp_path / "pgdata"]


# --- status and create ---------------------------------------------------------------


def test_db_status_distinguishes_running_stopped_and_unprovisioned(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)
    assert "30763" in runner.invoke(app, ["db", "status"]).stdout

    _install(monkeypatch, tmp_path / "b", running=False)
    assert "db start" in runner.invoke(app, ["db", "status"]).stdout

    _install(monkeypatch, tmp_path / "c", running=False, provisioned=False)
    assert "setup" in runner.invoke(app, ["db", "status"]).stdout


def test_db_status_always_exits_zero(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path, running=False, provisioned=False)
    assert runner.invoke(app, ["db", "status"]).exit_code == 0


def test_db_create_makes_the_database_on_the_bundled_server(monkeypatch, tmp_path) -> None:
    _, _, created = _install(monkeypatch, tmp_path)
    result = runner.invoke(app, ["db", "create", "fantabot_test"])
    assert result.exit_code == 0
    assert created == [
        ("postgresql+psycopg2://postgres:@/postgres?host=/Users/x/.fantabot/pgdata",
         "fantabot_test")
    ]


def test_db_create_refuses_a_name_that_is_not_an_identifier(monkeypatch, tmp_path) -> None:
    _, _, created = _install(monkeypatch, tmp_path)
    result = runner.invoke(app, ["db", "create", 'x"; DROP DATABASE fantabot; --'])
    assert result.exit_code == 1
    assert created == []


def test_db_create_on_a_stopped_server_exits_2(monkeypatch, tmp_path) -> None:
    _, _, created = _install(monkeypatch, tmp_path, running=False)
    assert runner.invoke(app, ["db", "create", "fantabot_test"]).exit_code == 2
    assert created == []


def test_db_start_prints_the_dsn_and_an_export_line(monkeypatch, tmp_path) -> None:
    """The DSN holds a `?`, which zsh globs — the export line must be single-quoted."""
    external = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"
    _install(monkeypatch, tmp_path, env={ENV_DATABASE_URL: external})
    result = runner.invoke(app, ["db", "start"])
    assert result.exit_code == 0
    assert f"export {ENV_DATABASE_URL}='{external}'" in result.stdout


def test_up_does_not_stop_the_server_on_exit(monkeypatch, tmp_path) -> None:
    """The bundled server's lifetime is explicit: `up` returning must not take it down."""
    _, stopped, _ = _install(monkeypatch, tmp_path, env={ENV_DATABASE_URL: DSN})
    monkeypatch.setattr("fantabot_app.keyfile.load_or_create_key", lambda **kw: b"k")
    monkeypatch.setattr("fantabot_app.server.serve", lambda: None)

    result = runner.invoke(app, ["up"])

    assert result.exit_code == 0
    assert stopped == []


def test_pgdata_lives_under_fantabot_home() -> None:
    directory = paths.pgdata()
    assert directory.name == "pgdata"
    assert directory.parent.name == ".fantabot"


def test_logs_live_under_fantabot_home() -> None:
    directory = paths.logs()
    assert directory.name == "logs"
    assert directory.parent.name == ".fantabot"


def test_home_is_the_fantabot_dir() -> None:
    assert paths.home().name == ".fantabot"


# --- stop refuses while a collector runs (SPEC §3.4) -----------------------------------
#
# A three-hour asta evening is exactly when a stray `stop` costs records, and the landing
# zone's guarantee is about kills it did not choose. `db stop` is deliberately untouched:
# it addresses the pgdata path and knows nothing about collectors.


@pytest.fixture
def harvest_home(monkeypatch, tmp_path):
    home = tmp_path / "aste_live"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FANTABOT_HARVEST_DIR", str(home))
    return home


@contextmanager
def _collector_holding(home):
    from fantabot.adapters.files.lock import COLLECTOR, role_lock

    with role_lock(home / "live.jsonl", COLLECTOR):
        yield home / "live.jsonl"


def test_stop_refuses_while_a_collector_holds_the_lock(monkeypatch, tmp_path, harvest_home):
    _, stopped, _ = _install(monkeypatch, tmp_path)

    with _collector_holding(harvest_home) as landing:
        result = runner.invoke(app, ["stop"])

    assert result.exit_code != 0
    assert "collector" in result.output
    assert str(landing) in result.output
    # Postgres included: the refusal is "stopped nothing", not "stopped most things".
    assert stopped == []


def test_the_refusal_says_how_to_stop_the_collector_deliberately(
    monkeypatch, tmp_path, harvest_home
):
    _install(monkeypatch, tmp_path)

    with _collector_holding(harvest_home):
        result = runner.invoke(app, ["stop"])

    assert "--force" in result.output


def test_stop_with_no_collector_running_stops_postgres(monkeypatch, tmp_path, harvest_home):
    _, stopped, _ = _install(monkeypatch, tmp_path)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert stopped == [tmp_path / "pgdata"]


def test_force_runs_the_stop_sequence_and_then_stops_postgres(
    monkeypatch, tmp_path, harvest_home
):
    _, stopped, _ = _install(monkeypatch, tmp_path)
    asked: list = []
    monkeypatch.setattr(cli, "_stop_collector", lambda landing: asked.append(landing) or True)

    with _collector_holding(harvest_home) as landing:
        result = runner.invoke(app, ["stop", "--force"])

    assert result.exit_code == 0
    assert asked == [landing]
    assert stopped == [tmp_path / "pgdata"]


def test_force_says_so_when_the_collector_could_not_be_stopped(
    monkeypatch, tmp_path, harvest_home
):
    """The launcher has no handle on a collector it did not start — only the app that
    spawned it can run the documented sequence. Saying so is better than implying the
    collector is gone, and Postgres still stops: it is not on the collection path."""
    _, stopped, _ = _install(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_stop_collector", lambda landing: False)

    with _collector_holding(harvest_home):
        result = runner.invoke(app, ["stop", "--force"])

    assert result.exit_code == 0
    assert "still" in result.output.lower()
    assert stopped == [tmp_path / "pgdata"]


def test_db_stop_knows_nothing_about_collectors(monkeypatch, tmp_path, harvest_home):
    """It addresses the pgdata path. A `db stop` that consulted the harvest home would be
    a second, different answer to "is it safe to stop" in the same CLI."""
    _, stopped, _ = _install(monkeypatch, tmp_path)

    with _collector_holding(harvest_home):
        result = runner.invoke(app, ["db", "stop"])

    assert result.exit_code == 0
    assert stopped == [tmp_path / "pgdata"]
