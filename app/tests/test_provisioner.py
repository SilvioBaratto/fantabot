"""F2 — the Postgres provisioner, migration and chromium steps.

All faked: no real Postgres, no alembic, no network. The provisioner's job is
orchestration — start the server, ensure the `fantabot` database, export
`FANTABOT_DATABASE_URL` so fantabot's lazy engine finds it — and that logic is what
these pin. The real `pixeltable_pgserver` start is exercised by the opt-in integration
test and the cross-OS cold-install CI (SPEC A1), not here.
"""

from __future__ import annotations

from fantabot_app.provisioner.chromium import install_chromium
from fantabot_app.provisioner.migrate import upgrade_head
from fantabot_app.provisioner.postgres import ENV_DATABASE_URL, PostgresProvisioner


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
