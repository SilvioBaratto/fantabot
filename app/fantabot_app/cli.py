"""The ``fantabot-app`` launcher CLI.

A thin Typer app — ``setup`` / ``up`` / ``stop`` / ``doctor``, plus the ``db`` group —
mirroring fantabot's own Typer convention. Running ``fantabot-app`` with no subcommand is
the everyday launch and defaults to ``up``.

**The bundled server's lifetime is explicit.** ``db start`` leaves Postgres running after
the command exits, and only ``fantabot-app stop`` / ``db stop`` ever takes it down. That
is what lets a plain ``fantabot`` CLI invocation — from any directory, with the app not
running — reach the same database the app uses.

``db url`` is a *return value*, not a message: it is consumed as
``FANTABOT_DATABASE_URL="$(fantabot-app db url --database fantabot_test)"``, so the DSN is
the only thing on stdout and a missing DSN exits 2 rather than printing an empty string.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from fantabot_app.provisioner.postgres import PostgresProvisioner

app = typer.Typer(
    name="fantabot-app",
    help="Local, user-friendly UI over fantabot — one command, no Docker.",
    no_args_is_help=False,
    add_completion=False,
)
ENV_URL = "FANTABOT_DATABASE_URL"
db_app = typer.Typer(no_args_is_help=True, help="Start, stop and inspect the bundled Postgres.")
app.add_typer(db_app, name="db")
harvest_app = typer.Typer(no_args_is_help=True, help="The harvest home under ~/.fantabot.")
app.add_typer(harvest_app, name="harvest")
#: Where the artefacts were before the home existed. A module constant rather than a
#: `Path(...)` in the option, which ruff reads as a call in a default (B008).
LEGACY_HARVEST_DIR = Path("./data/aste_live")


def _provisioner() -> PostgresProvisioner:
    """The one place a provisioner is built — the seam the CLI tests replace."""
    from fantabot_app.provisioner.postgres import PostgresProvisioner

    return PostgresProvisioner()


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Run ``up`` when invoked with no subcommand (the everyday launch)."""
    if ctx.invoked_subcommand is None:
        up()


@app.command()
def setup() -> None:
    """Provision local Postgres, run migrations, and install chromium."""
    from fantabot_app import keyfile
    from fantabot_app.provisioner import chromium, migrate

    provisioner = _provisioner()
    typer.echo("Provisioning local Postgres (bundled PG18, no Docker)...")
    url = provisioner.start()
    typer.echo(f"Postgres ready at {_redact(url)}")
    typer.echo("Preparing the encryption key...")
    keyfile.load_or_create_key(create=True)  # mint once; never echo the key itself
    typer.echo("Running migrations (alembic upgrade head)...")
    migrate.upgrade_head()
    typer.echo("Installing chromium for headed login...")
    chromium.install_chromium()
    typer.echo("Setup complete.")


@app.command()
def up() -> None:
    """Start Postgres if needed, boot the API + UI, and open the browser."""
    from fantabot_app import keyfile, server

    typer.echo("Starting local Postgres...")
    _provisioner().start()
    # Load (or mint, if setup was skipped) the encryption key into the environment before
    # the app starts, so connecting an account just works — no manual configuration.
    keyfile.load_or_create_key(create=True)
    typer.echo("Serving fantabot-app at http://127.0.0.1:8000 (Ctrl-C to stop)...")
    server.serve()


#: Where the app serves. `up` prints it; the stop sequence below asks it for the running
#: collector, because the process that spawned a child is the only one that can stop it
#: the documented way.
API_BASE = "http://127.0.0.1:8000/api/v1"

#: The bound on waiting for a collector to let go, and how often the wait looks. The same
#: numbers as the supervisor's own sequence — see `api/infrastructure/processes.py`.
COLLECTOR_STOP_GRACE_S = 15.0
COLLECTOR_STOP_POLL_S = 0.25


def _landing() -> Path:
    """The landing zone of the harvest home, resolved when the command runs."""
    from fantabot.config import harvest_dir

    return Path(harvest_dir()) / "live.jsonl"


def _collector_running(landing: Path) -> bool:
    """Ask the operating system, by trying to take the role for a moment.

    An advisory lock and not a pid file, and `adapters/files/lock.py` says why: the kernel
    releases it however the holder dies, so this stays correct across a crash, a `SIGKILL`
    and an app restart. Taking it and letting go is the *question*; nothing here holds it.
    """
    from fantabot.adapters.files.lock import COLLECTOR, RoleBusy, role_lock

    try:
        with role_lock(landing, COLLECTOR):
            return False
    except RoleBusy:
        return True


def _stop_collector(landing: Path) -> bool:
    """Run the documented stop sequence, through the process that owns the child.

    The launcher has no handle on the collector: it is a child of the *server*, and the
    server is where SIGINT-then-lock-then-SIGKILL lives. So this asks it over the loopback
    API rather than reimplementing the sequence against a pid it would have to guess.

    Returns whether the collector is gone. `False` is the honest answer for a collector
    started at a terminal, or with the app not running — neither is reachable from here,
    and implying otherwise would be worse than saying so.
    """
    import json
    import time
    import urllib.error
    import urllib.request

    def call(path: str, method: str) -> object | None:
        # A fixed loopback URL, not composed from anything the operator typed.
        request = urllib.request.Request(f"{API_BASE}{path}", method=method)
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                parsed: object = json.loads(response.read().decode("utf-8"))
                return parsed
        except (urllib.error.URLError, OSError, ValueError):
            return None

    listing = call("/jobs", "GET")
    jobs = listing.get("jobs", []) if isinstance(listing, dict) else []
    live = next(
        (
            job
            for job in jobs
            if job.get("kind") == "harvest-collect" and job.get("status") == "running"
        ),
        None,
    )
    if live is None:
        return False
    if call(f"/jobs/{live['id']}/stop", "POST") is None:
        return False

    deadline = time.monotonic() + COLLECTOR_STOP_GRACE_S
    while time.monotonic() < deadline:
        if not _collector_running(landing):
            return True
        time.sleep(COLLECTOR_STOP_POLL_S)
    return not _collector_running(landing)


@app.command()
def stop(
    force: Annotated[
        bool, typer.Option("--force", help="Stop even while a collector is running.")
    ] = False,
) -> None:
    """Stop the bundled Postgres — refusing while a collector is running.

    A three-hour asta evening is exactly when a stray `stop` costs records, and the
    landing zone's guarantee is about kills it did not choose. So the default is the
    answer that cannot lose them, and `--force` is the other one.

    The refusal stops **nothing**, Postgres included. Not because Postgres would harm a
    collector — it cannot, which is the whole point of the landing zone — but because
    "stopped most things" is not an answer anyone can act on at 21:47.

    `fantabot-app db stop` is deliberately untouched: it addresses the pgdata path and
    knows nothing about collectors. Two different answers to "is it safe to stop" inside
    one CLI would be worse than one.
    """
    landing = _landing()
    if _collector_running(landing):
        if not force:
            typer.echo(f"A collector is running: it holds {landing}.", err=True)
            typer.echo(
                "Stopped nothing, Postgres included. Stop it from the app's Harvest page, "
                "or Ctrl-C the terminal it runs in — or re-run with --force.",
                err=True,
            )
            raise typer.Exit(code=1)
        if _stop_collector(landing):
            typer.echo("Collector stopped.")
        else:
            # Honest rather than reassuring: only the process that spawned the child can
            # run the documented sequence. Postgres still stops — it is not on the
            # collection path, and that is exactly why an outage costs catch-up and never
            # a record.
            typer.echo(
                "The collector is still running — it was not started by an app this "
                f"launcher can reach. Stop it where it runs; it holds {landing}.",
                err=True,
            )
    db_stop()


@app.command()
def doctor() -> None:
    """Report on python, fantabot, Postgres, the database, and chromium."""
    from fantabot_app.doctor import run_checks

    failed = 0
    for check in run_checks():
        mark = "OK" if check.ok else "XX"
        if not check.ok:
            failed += 1
        typer.echo(f"[{mark}] {check.name}: {check.detail}")
    if failed:
        typer.echo(f"\n{failed} check(s) failed.")
        raise typer.Exit(code=1)


@db_app.command("start")
def db_start() -> None:
    """Start the bundled Postgres and leave it running after this command exits."""
    provisioner = _provisioner()
    external = provisioner.status()["external"]
    url = provisioner.start()
    if external:
        typer.echo(
            f"{ENV_URL} is exported; started nothing and using that database.", err=True
        )
    else:
        typer.echo(f"Postgres ready at {url}")
    # Single-quoted: the socket DSN carries a `?`, which the shell would glob.
    typer.echo(f"export {ENV_URL}='{url}'")


@db_app.command("stop")
def db_stop() -> None:
    """Stop the bundled Postgres, whichever process started it."""
    provisioner = _provisioner()
    if provisioner.status()["external"]:
        typer.echo(
            f"{ENV_URL} is exported and not consulted here; "
            f"this stops the bundled server at {provisioner.status()['pgdata']}.",
            err=True,
        )
    outcome = provisioner.stop_bundled()
    typer.echo(
        {
            "stopped": "Postgres stopped.",
            "not_running": "Postgres is not running.",
            "not_provisioned": "No bundled Postgres here — run `fantabot-app setup`.",
        }[outcome]
    )


@db_app.command("status")
def db_status() -> None:
    """Report the bundled Postgres: provisioned, running, and at which DSN."""
    state = _provisioner().status()
    if state["external"]:
        typer.echo(f"{ENV_URL} is exported: {_redact(str(state['url']))} (running: unknown)")
    elif state["running"]:
        typer.echo(f"running (pid {state['pid']}) at {state['url']}")
    elif state["provisioned"]:
        typer.echo(f"provisioned at {state['pgdata']}, stopped — run `fantabot-app db start`")
    else:
        typer.echo(f"not provisioned at {state['pgdata']} — run `fantabot-app setup`")


@db_app.command("url")
def db_url(
    database: str = typer.Option(
        "", "--database", help="Name another database on the same server (e.g. fantabot_test)."
    ),
) -> None:
    """Print the DSN, and nothing else, so a shell can capture it.

    With no ``--database`` an exported ``FANTABOT_DATABASE_URL`` is returned verbatim —
    including any password, because the caller is about to connect with it. With
    ``--database`` the bundled server is always addressed: answering with the exported URL
    would hand back the canonical database under another database's name.
    """
    provisioner = _provisioner()
    state = provisioner.status()
    if state["external"] and not database:
        typer.echo(str(state["url"]))
        return
    if state["external"]:
        typer.echo(
            f"{ENV_URL} is exported; --database addresses the bundled server "
            f"at {state['pgdata']}.",
            err=True,
        )
    url = provisioner.bundled_url(database=database or None)
    if url is None:
        typer.echo(_down(state), err=True)
        raise typer.Exit(code=2)
    typer.echo(url)


@db_app.command("create")
def db_create(name: str = typer.Argument(..., help="Database to create if absent.")) -> None:
    """Create a database on the bundled server (``fantabot_test``, for the db test tier)."""
    provisioner = _provisioner()
    state = provisioner.status()
    if not state["running"]:
        typer.echo(_down(state), err=True)
        raise typer.Exit(code=2)
    try:
        provisioner.create_database(name)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f'database "{name}" ready')


@harvest_app.command("adopt")
def harvest_adopt(
    # `Annotated` rather than this file's older `= typer.Option(...)` style, because a
    # call in a default is B008 for every annotation ruff does not know to be immutable —
    # `str` is, `Path` is not, which is why no other command here had to say it this way.
    source: Annotated[
        Path, typer.Option("--from", help="Directory the artefacts are in today.")
    ] = LEGACY_HARVEST_DIR,
) -> None:
    """Move an existing landing zone, seed and bridge into the harvest home.

    One-time, and idempotent: a second run finds nothing to move and says so. It refuses
    rather than moving half of a landing zone — see ``harvest_home`` for which halves.
    """
    from fantabot_app import harvest_home

    try:
        report = harvest_home.adopt(source, harvest_home.harvest_dir())
    except harvest_home.AdoptRefused as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if not report.moved and not report.skipped:
        typer.echo(f"nothing to adopt in {source} — the harvest home is {report.destination}")
        return
    typer.echo(
        f"moved {report.moved} file(s) ({report.total_bytes} bytes) into {report.destination}"
        + (f", {report.skipped} already there" if report.skipped else "")
    )


def _down(state: dict[str, object]) -> str:
    """Why there is no DSN: never provisioned, or provisioned and stopped."""
    if not state["provisioned"]:
        return f"no bundled Postgres at {state['pgdata']} — run `fantabot-app setup`"
    return "the bundled Postgres is not running; start it with `fantabot-app db start`"


def _redact(url: str) -> str:
    """Hide any password in a database URL before echoing it."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, _, host = rest.partition("@")
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}@{host}"


def main() -> None:
    """Console-script entry point (``fantabot-app``)."""
    app()


if __name__ == "__main__":
    main()
