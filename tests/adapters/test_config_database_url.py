"""The CLI's default database is the app's bundled Postgres, not a compose port.

`.env` sent the CLI to `localhost:54321` while `fantabot-app` provisioned its own server
at `~/.fantabot/pgdata` — the split that let a week of Classic auction collection read as
lost (`todo/TODO.md` §1). The default now derives from the bundled data directory, so a
`fantabot` command run from any directory reaches the database the app writes to.

Two things the derivation must get right, both of which a hardcoded string cannot:

* **The DSN shape is not fixed.** pgserver runs on a unix socket on macOS/Linux and on
  127.0.0.1 with a chosen port on Windows, so the default reads `postmaster.pid` when it
  is there rather than assuming one of the two.
* **The socket path is never percent-encoded.** alembic's config is a `ConfigParser`,
  which interpolates `%` and rejects a `%2F`-encoded path outright — verified: a
  percent-encoded DSN raises `ValueError: invalid interpolation syntax`.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from fantabot.config import Settings, bundled_database_url


@pytest.fixture(autouse=True)
def _no_ambient_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported FANTABOT_DATABASE_URL would decide every assertion here."""
    monkeypatch.delenv("FANTABOT_DATABASE_URL", raising=False)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def _pid_file(home: Path, lines: list[str]) -> Path:
    pgdata = home / ".fantabot" / "pgdata"
    pgdata.mkdir(parents=True, exist_ok=True)
    (pgdata / "postmaster.pid").write_text("\n".join(lines) + "\n")
    return pgdata


def _running(home: Path, *, socket_dir: str = "", hostname: str = "", port: str = "5432") -> Path:
    pgdata = home / ".fantabot" / "pgdata"
    return _pid_file(
        home,
        ["30763", str(pgdata), "1788532310", port, socket_dir or str(pgdata), hostname, "shm", "ready"],
    )


def test_the_default_is_the_bundled_server_not_a_compose_port(home: Path) -> None:
    url = Settings(_env_file=None).fantabot_database_url

    assert "54321" not in url
    assert url.startswith("postgresql+psycopg2://")
    assert str(home / ".fantabot" / "pgdata") in url
    assert url.endswith("/fantabot?host=" + str(home / ".fantabot" / "pgdata"))


def test_the_socket_path_is_not_percent_encoded(home: Path) -> None:
    """`%` is what breaks alembic, and percent-encoding is what puts one there."""
    assert "%2F" not in Settings(_env_file=None).fantabot_database_url
    assert "%" not in Settings(_env_file=None).fantabot_database_url


def test_a_running_server_is_read_from_its_pid_file(home: Path) -> None:
    """The socket directory is pgserver's choice, not ours — it may sit under TMPDIR."""
    _running(home, socket_dir="/tmp/pg-xyz")

    assert bundled_database_url() == "postgresql+psycopg2://postgres:@/fantabot?host=/tmp/pg-xyz"


def test_a_windows_server_is_addressed_over_tcp(home: Path) -> None:
    """There is no unix socket on Windows: pgserver binds 127.0.0.1 on a chosen port."""
    _running(home, socket_dir=" ", hostname="127.0.0.1", port="55123")

    assert bundled_database_url() == "postgresql+psycopg2://postgres:@127.0.0.1:55123/fantabot"


def test_a_torn_pid_file_falls_back_to_the_socket_form(home: Path) -> None:
    """A file half-written by a crashing postmaster must not turn `fantabot --help` into a
    traceback — every command reads this default at import."""
    _pid_file(home, ["30763", str(home)])

    assert bundled_database_url().endswith("?host=" + str(home / ".fantabot" / "pgdata"))


def test_another_database_on_the_same_server(home: Path) -> None:
    """What `fantabot_test_database_url` will be built from (T2.6)."""
    _running(home, socket_dir="/tmp/pg-xyz")

    assert bundled_database_url("fantabot_test") == (
        "postgresql+psycopg2://postgres:@/fantabot_test?host=/tmp/pg-xyz"
    )


def test_an_exported_url_still_wins(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FANTABOT_DATABASE_URL", "postgresql+psycopg2://u:p@example.test:5/db")

    assert Settings(_env_file=None).fantabot_database_url == (
        "postgresql+psycopg2://u:p@example.test:5/db"
    )


def test_the_compose_port_settings_are_gone() -> None:
    """Nothing but `docker-compose.yml` ever read them, and it is being deleted (T2.4)."""
    fields = Settings.model_fields

    assert "fantabot_db_host_port" not in fields
    assert "fantabot_adminer_host_port" not in fields
