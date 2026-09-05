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


@app.command()
def stop() -> None:
    """Stop the bundled Postgres (the same thing as ``fantabot-app db stop``)."""
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
