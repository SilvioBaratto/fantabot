"""Where a dump lands, what runs it, and the refusal that keeps it off this volume (T26).

One local server holds everything, and that is the entire durability story: losing
`~/.fantabot/pgdata` destroys both 50,634-row match-grain tables, and re-scraping them is
roughly 750 GETs per season against a site under no obligation to keep serving 2022/23.
So the dump matters, and where it lands matters more than it looks.

**The path is the deliverable, which is why it is derived here rather than in a Typer
body.** `tasks/archive/parity-spec.md` §8 Never #4 forbids the app from offering a browser download of a dump:
handing over the bytes puts the file wherever the browser puts downloads, a directory
neither of the two guards covers. What the app offers instead is the path — and a path
named by a surface that did not derive it is how two surfaces come to disagree about
where the dump is. `dump_target` is the one answer; both surfaces ask it.

**Both refusals are inherited, not re-stated.** The dump carries the `league_tokens`
rows — encrypted, but still credentials — so it never lands inside the working tree,
which is one `git add -A` from a commit with a remote to push it to; `.gitignore`'s
`*.dump`/`*.sql` is the second layer and covers only the accident this one is about.
`$HOME` is also a different volume from the external drive, so the dump survives the
unmounting it exists to survive.

**The refusal happens before `pg_dump` is spawned, and raises rather than returning.** A
dump of this database is ~2 GB and minutes; discovering at the end that it landed on the
volume it was meant to survive the loss of is a discovery worth making first. A returned
sentinel is one a caller can forget to read, and the caller that forgets is the one
running unattended.

**The clock is a parameter**, for `application/scrape.py`'s reason: the filename is a
date, so a module that read the clock would have tests that go red at midnight.

`run` is injected for the same reason the browser is injected into
`application/auth_login.py` — so the suite can prove what happens when `pg_dump` is
absent and when it exits non-zero without either being true of the machine running it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO

#: The prefix macOS mounts removable and external volumes under. The repository lives on
#: one by design, so this is not a hypothetical: it is the guard the deleted
#: `scripts/db_dump.sh` carried, and the only reason the dump is not written beside the
#: thing it is a backup of. Linux and Windows never match it, which is correct rather
#: than lucky — neither mounts `$HOME` there.
EXTERNAL_VOLUME_PREFIX = "/Volumes/"


class DumpRefused(ValueError):
    """The dump was not attempted, because the path it would take is not one to take.

    Named so each surface refuses its own way — a `typer.Exit(1)` with the line printed,
    a named outcome with the line in the body — without either having to tell this apart
    from a database being down. Nothing ran, so nothing has to be cleaned up.
    """


class PgDumpMissing(RuntimeError):
    """`pg_dump` is not on PATH. Kept apart from `PgDumpFailed` because the remedies are.

    The bundled server ships one: it lives beside the `postgres` binary in
    `pixeltable_pgserver`'s `pginstall18/bin`. That is a sentence about installation.
    A non-zero exit is a sentence about the server being down, and one screen over two
    remedies is the defect `api/outcomes.py` exists for.
    """


class PgDumpFailed(RuntimeError):
    """`pg_dump` ran and exited non-zero — almost always a database that is not up."""


@dataclass(frozen=True)
class DumpWrote:
    """Where the dump is, and how much of it there is.

    The size is measured off the file rather than counted by the caller, because it is
    the only evidence available that `pg_dump` wrote anything at all: it exits 0 against
    an empty database too, and a 0-byte dump is the failure worth noticing.
    """

    path: Path
    size_bytes: int


def dump_target(home: Path, today: date) -> Path:
    """`<home>/fantabot-db-YYYYMMDD.dump`, or the reason that is not a place to write.

    One file per day rather than per run: a second dump on the same day overwrites the
    first, which is the behaviour worth having — the failure this protects against is a
    dead disk, not an edit five minutes ago, and a directory that grows a 400 MB file
    per invocation is one nobody prunes.
    """
    target = home / f"fantabot-db-{today:%Y%m%d}.dump"
    if str(target.resolve()).startswith(EXTERNAL_VOLUME_PREFIX):
        raise DumpRefused(
            f"refusing to write the dump onto an external volume: {target}"
        )
    return target


def pg_dump_argv(database_url: str) -> list[str]:
    """`pg_dump` addressing whatever the DSN addresses, in custom format.

    The driver suffix is stripped because `postgresql+psycopg2://` is SQLAlchemy's
    spelling and libpq does not know it; everything else — user, password, host, and the
    `?host=` socket directory the bundled server uses — is a valid libpq connection URI
    already, so the DSN is handed over whole rather than picked apart into flags.

    Custom format (`-Fc`) is what makes `pg_restore` usable and selective; a plain SQL
    dump is a different artefact that happens to hold the same rows.
    """
    return ["pg_dump", "-Fc", database_url.replace("+psycopg2://", "://", 1)]


def _subprocess_pg_dump(argv: list[str], stdout: IO[bytes]) -> int:
    """The real seam. Imported here so importing this module costs nothing."""
    import subprocess

    return subprocess.run(argv, stdout=stdout).returncode


def run_dump(
    target: Path,
    database_url: str,
    *,
    run: Callable[[list[str], IO[bytes]], int] = _subprocess_pg_dump,
) -> DumpWrote:
    """Stream the database into `target`. The only thing here that touches the world.

    `target` is passed in rather than derived, so the path an operator was shown before
    the run is the path the run writes — deriving it twice is how a dump comes to be
    somewhere other than where the screen said it would be.
    """
    try:
        try:
            with target.open("wb") as handle:
                code = run(pg_dump_argv(database_url), handle)
        except FileNotFoundError:
            raise PgDumpMissing(
                "pg_dump is not on PATH. The bundled server ships one: it lives beside "
                "the `postgres` binary in pixeltable_pgserver's `pginstall18/bin`."
            ) from None
        if code != 0:
            raise PgDumpFailed(
                f"pg_dump exited {code} — is the database running? "
                "Start it with: fantabot-app db start"
            )
    except BaseException:
        # One removal site for all three ways out, `KeyboardInterrupt` included: the
        # app's stop button reaches the child as `SIGINT` somewhere inside the stream,
        # and the CLI had no way to ask for that at all.
        #
        # Removed rather than reported. A half-written dump sits at the path both
        # surfaces name, is the right size to look real, and is refused by `pg_restore`
        # only at the moment it is needed — which is the moment the disk it was
        # protecting against is already gone. An absent dump is a fact an operator can
        # see today.
        target.unlink(missing_ok=True)
        raise
    return DumpWrote(path=target, size_bytes=target.stat().st_size)
