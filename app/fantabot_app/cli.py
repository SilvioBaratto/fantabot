"""The ``fantabot-app`` launcher CLI.

A thin Typer app with four commands — ``setup`` / ``up`` / ``stop`` / ``doctor`` —
mirroring fantabot's own Typer convention. Running ``fantabot-app`` with no subcommand
is the everyday launch and defaults to ``up``.

This is the F1 skeleton: the command surface is fixed here so later tasks fill in the
bodies (F2 provisioner + migrate for ``setup``/``stop``/``doctor``, F4 server host for
``up``). Bodies currently announce which task implements them rather than doing work.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="fantabot-app",
    help="Local, user-friendly UI over fantabot — one command, no Docker.",
    no_args_is_help=False,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Run ``up`` when invoked with no subcommand (the everyday launch)."""
    if ctx.invoked_subcommand is None:
        up()


@app.command()
def setup() -> None:
    """Provision local Postgres, run migrations, and install chromium."""
    from fantabot_app.provisioner import chromium, migrate
    from fantabot_app.provisioner.postgres import PostgresProvisioner

    provisioner = PostgresProvisioner()
    typer.echo("Provisioning local Postgres (bundled PG18, no Docker)...")
    url = provisioner.start()
    typer.echo(f"Postgres ready at {_redact(url)}")
    typer.echo("Running migrations (alembic upgrade head)...")
    migrate.upgrade_head()
    typer.echo("Installing chromium for headed login...")
    chromium.install_chromium()
    typer.echo("Setup complete.")


@app.command()
def up() -> None:
    """Start Postgres if needed, boot the API + UI, and open the browser."""
    typer.echo("up: not yet implemented (F4 — server host)")


@app.command()
def stop() -> None:
    """Stop the local Postgres the launcher started."""
    from fantabot_app.provisioner.postgres import PostgresProvisioner

    PostgresProvisioner().stop()
    typer.echo("Postgres stopped.")


@app.command()
def doctor() -> None:
    """Report on uv, Postgres, migrations, chromium, and token status."""
    typer.echo("doctor: not yet implemented (F2 — extended checks)")


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
