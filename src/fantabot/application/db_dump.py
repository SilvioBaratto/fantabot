"""Where a dump lands, what runs it, and the refusal that keeps it off this volume (T26).

One local server holds everything, and that is the entire durability story: losing
`~/.fantabot/pgdata` destroys both 50,634-row match-grain tables, and re-scraping them is
roughly 750 GETs per season against a site under no obligation to keep serving 2022/23.
So the dump matters, and where it lands matters more than it looks.

**The path is the deliverable, which is why it is derived here rather than in a Typer
body.** The parity phase's spec §8, Never #4 (archived, not in this checkout) forbids the
app from offering a browser download of a dump: handing over the bytes puts the file
wherever the browser puts downloads, a directory neither of the two guards covers. What
the app offers instead is the path — and a path
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

**Which `pg_dump` runs is chosen, not inherited from PATH.** A `pg_dump` dumps a server of
its own version or older and refuses a newer one, and the bundled server (PostgreSQL 18) is
newer than the client most machines carry on PATH — Homebrew's 17.5 on the machine that hit
it. `resolve_pg_dump` weighs the bundled server's own client against PATH's by major version,
the bundled one winning a tie because it is the one built with the server it dumps.

The bundled client is found where the *server* says it is, before the wheel is asked. The
server's `postmaster.opts` records the exact `postgres` it was started from, and `pg_dump`
sits beside it. The wheel is a dependency of the app and not of this library, so an
interpreter that has only the library — the operator's own `fantabot` — cannot import it, and
a lookup that needed the import would pick PATH's older client in precisely the interpreter
the operator types into. The wheel's table stays as the fallback for a server that has not
been started yet.

**The clock is a parameter**, for `application/scrape.py`'s reason: the filename is a
date, so a module that read the clock would have tests that go red at midnight.

`run` is injected for the same reason the browser is injected into
`application/auth_login.py` — so the suite can prove what happens when `pg_dump` is
absent and when it exits non-zero without either being true of the machine running it.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

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
    """No `pg_dump` anywhere: not with the bundled server and not on PATH.

    Kept apart from `PgDumpFailed` because the remedies are. This is a sentence about
    installation; a non-zero exit is a sentence about what the one that ran reported. One
    screen over two remedies is the defect `api/outcomes.py` exists for.
    """


class PgDumpFailed(RuntimeError):
    """`pg_dump` ran and exited non-zero.

    The message carries what it said on stderr, and names a cause only when stderr did. A
    non-zero exit has many causes, and advice about the one that stderr did not show —
    "is the database running?" over a server that is up — sends the operator to the wrong
    place.
    """


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


def pg_dump_argv(database_url: str, executable: str = "pg_dump") -> list[str]:
    """`pg_dump` addressing whatever the DSN addresses, in custom format.

    The driver suffix is stripped because `postgresql+psycopg2://` is SQLAlchemy's
    spelling and libpq does not know it; everything else — user, password, host, and the
    `?host=` socket directory the bundled server uses — is a valid libpq connection URI
    already, so the DSN is handed over whole rather than picked apart into flags.

    Custom format (`-Fc`) is what makes `pg_restore` usable and selective; a plain SQL
    dump is a different artefact that happens to hold the same rows.

    Args:
        database_url: The SQLAlchemy DSN of the database to dump.
        executable: What to run. The bare name is the default so the argv stays a pure
            function of the DSN; the real seam swaps in the resolved binary.
    """
    return [executable, "-Fc", database_url.replace("+psycopg2://", "://", 1)]


@dataclass(frozen=True)
class PgDumpBinary:
    """One `pg_dump` the machine can run, and where it was found.

    Attributes:
        path: What to exec.
        major: The PostgreSQL major version it reports, or `None` when it would not say.
        source: `"bundled"` for the bundled server's own client, `"PATH"` otherwise.
    """

    path: str
    major: int | None
    source: Literal["bundled", "PATH"]


@dataclass(frozen=True)
class DumpExit:
    """How `pg_dump` ended: its exit code, and the stderr that explains a non-zero one.

    A seam may still return a bare `int`; `run_dump` reads both. The richer form exists so
    a failure can say what happened instead of guessing at it.
    """

    code: int
    stderr: str = ""


def parse_pg_dump_major(banner: str | None) -> int | None:
    """The major version in a `pg_dump --version` banner, or `None` when there is none.

    Handles release (`17.5`), pre-release (`19devel`, `16beta2`) and distributor-suffixed
    (`18.4 (Homebrew)`) banners alike: the number right after the product name is the major.
    `None` rather than 0, so an unreadable banner never ranks as the oldest version.
    """
    if not banner:
        return None
    found = re.search(r"\(PostgreSQL\)\s+(\d+)", banner)
    return int(found.group(1)) if found else None


def _server_pg_dump() -> tuple[str, int] | None:
    """The `pg_dump` beside the `postgres` the bundled server was started from, and its major.

    `postmaster.opts` is the first line of the server's own record of how it was launched:
    the executable's full path, unquoted and possibly holding spaces, then each argument in
    double quotes. So the path is whatever precedes the first quoted argument, never a
    whitespace split. The major is the data directory's `PG_VERSION` — the server's own
    answer, with no process to spawn. `None` when any of it is absent or unreadable, or the
    install it names has since moved: this is a lookup that is allowed to find nothing.
    """
    from fantabot.config import bundled_pgdata

    pgdata = bundled_pgdata()
    try:
        opts = (pgdata / "postmaster.opts").read_text(encoding="utf-8")
        major = int((pgdata / "PG_VERSION").read_text(encoding="utf-8").strip().split(".")[0])
    except (OSError, ValueError):
        return None
    server = opts.strip().partition(' "')[0].strip('"')
    if not server:
        return None
    # `which` rather than a path join: it is what knows the `.exe` suffix on Windows.
    found = shutil.which("pg_dump", path=str(Path(server).parent))
    return (found, major) if found else None


def _wheel_pg_dump() -> tuple[str, int] | None:
    """The server wheel's own `pg_dump` and its major, or `None` when it has none.

    The highest version in the wheel's table that actually ships a client: an install can
    list a server version without the client tools beside it. The wheel is imported here
    and only here, so the library stays importable without the app's dependencies.
    """
    try:
        from pixeltable_pgserver.utils import POSTGRES_VERSIONS
    except (ImportError, RuntimeError):
        # `RuntimeError` is the wheel's own refusal at import time when
        # `PGSERVER_POSTGRES_VERSION` names a major it does not ship — the same setting under
        # which it will not start its server, so no dump is wanted from it either.
        return None
    for major, bin_dir in sorted(POSTGRES_VERSIONS.items(), reverse=True):
        found = shutil.which("pg_dump", path=str(bin_dir))
        if found:
            return found, int(major)
    return None


def _bundled_pg_dump() -> tuple[str, int] | None:
    """The bundled server's own `pg_dump`: the one beside its running binary, else the wheel's.

    The running server's is first because it is exactly the client of the server being
    dumped. The wheel's is the fallback for a server never started, where `postmaster.opts`
    does not exist yet.
    """
    return _server_pg_dump() or _wheel_pg_dump()


def _pg_dump_on_path() -> str | None:
    return shutil.which("pg_dump")


def _probe_major(path: str) -> int | None:
    """Ask a `pg_dump` for its version. Any failure to ask is an unknown, not an error."""
    import subprocess

    try:
        done = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_pg_dump_major(done.stdout)


def resolve_pg_dump(
    *,
    bundled: Callable[[], tuple[str, int] | None] = _bundled_pg_dump,
    on_path: Callable[[], str | None] = _pg_dump_on_path,
    version_of: Callable[[str], int | None] = _probe_major,
) -> PgDumpBinary | None:
    """The `pg_dump` able to dump the server, or `None` when the machine has none.

    A client dumps a server of its own major or older, so the higher major is the safe
    choice. Ties go to the bundled one — it was built with the server it dumps — and so does
    a PATH binary that will not state its version: guessing that it is new enough is how
    a client older than the server gets picked. The bundled version is the server's own record
    of it and is not probed.

    Args:
        bundled: Returns the bundled server's `(path, major)`, or `None`.
        on_path: Returns the first `pg_dump` on PATH, or `None`.
        version_of: Returns the major version of the `pg_dump` at a path, or `None`.
    """
    chosen: PgDumpBinary | None = None
    from_server = bundled()
    if from_server is not None:
        chosen = PgDumpBinary(path=from_server[0], major=from_server[1], source="bundled")
    hit = on_path()
    if hit is not None:
        candidate = PgDumpBinary(path=hit, major=version_of(hit), source="PATH")
        if chosen is None or (candidate.major or -1) > (chosen.major or -1):
            chosen = candidate
    return chosen


def _subprocess_pg_dump(argv: list[str], stdout: IO[bytes]) -> DumpExit:
    """The real seam. Imported here so importing this module costs nothing."""
    import subprocess

    safe_argv, env = _password_to_environment(argv)
    # Only the bare default is replaced: a caller that named an executable meant it.
    if safe_argv[0] == "pg_dump":
        chosen = resolve_pg_dump()
        if chosen is not None:
            safe_argv = [chosen.path, *safe_argv[1:]]
    done = subprocess.run(safe_argv, stdout=stdout, stderr=subprocess.PIPE, env=env)
    stderr = (done.stderr or b"").decode("utf-8", errors="replace")
    if done.returncode == 0 and stderr:
        # stderr is piped so a failure can quote it, which leaves nothing printing it on a
        # clean run: a dump that succeeds with warnings still shows them.
        sys.stderr.write(stderr)
    return DumpExit(code=done.returncode, stderr=stderr)


def _password_to_environment(argv: list[str]) -> tuple[list[str], dict[str, str] | None]:
    """Move a password out of the DSN argument and into `PGPASSWORD` for the child.

    argv is readable by every local user in `ps` for the minutes a 2 GB dump runs; the
    child's environment is not. The bundled server's socket DSN carries no password, in
    which case nothing changes and the child inherits the environment as before.
    """
    parts = urlsplit(argv[-1])
    if not parts.password:
        return argv, None
    host = parts.netloc.rpartition("@")[2]
    user = parts.netloc.rpartition("@")[0].split(":", 1)[0]
    bare = urlunsplit(parts._replace(netloc=f"{user}@{host}"))
    return [*argv[:-1], bare], {**os.environ, "PGPASSWORD": unquote(parts.password)}


#: pg_dump's detail line. Each side is the number that opens the version, then anything up to
#: the `;` — a distributor appends a banner with spaces to both, e.g. `18.4 (Debian
#: 18.4-1.pgdg120+1)`, and a pattern that wanted one token would read that as no mismatch.
_VERSION_MISMATCH = re.compile(
    r"server version:\s*(?P<server>\d[\w.]*)[^;\n]*;\s*pg_dump version:\s*(?P<client>\d[\w.]*)"
)

#: Phrases libpq uses when the connection itself never came up. Matched in lower case on
#: the wording PostgreSQL has used across majors; a translated message matches nothing and
#: the failure is then reported without a cause, which is the honest answer.
_SERVER_UNREACHABLE = (
    "connection refused",
    "could not connect",
    "is the server running",
    "server closed the connection",
    "is starting up",
)

#: How much of pg_dump's stderr a failure carries. The cause is at the end, and a verbose
#: run must not turn an exception message into a log.
_STDERR_LINES = 8
_STDERR_CHARS = 1_500


def failure_message(code: int, stderr: str) -> str:
    """The sentence a non-zero `pg_dump` exit is reported as.

    A cause is named only when stderr showed one: a client older than the server, or a
    connection that never came up. Anything else is reported as what `pg_dump` said, and
    a silent exit is reported as silent — advice that the server is down, printed over a
    server that was up, is worse than none.

    Args:
        code: The exit code.
        stderr: Everything `pg_dump` wrote to stderr, already scrubbed of credentials.
    """
    mismatch = _VERSION_MISMATCH.search(stderr)
    if mismatch or "version mismatch" in stderr.lower():
        versions = (
            f"pg_dump {mismatch['client']} cannot dump a PostgreSQL {mismatch['server']} server"
            if mismatch
            else "pg_dump refused this server"
        )
        return (
            f"{versions} (version mismatch). A pg_dump at least as new as the server is "
            "needed. The bundled server's own is used automatically when it can be found, "
            "beside the server recorded in ~/.fantabot/pgdata/postmaster.opts or in the "
            "pixeltable_pgserver wheel; otherwise install PostgreSQL client tools of the "
            "server's major version."
        )
    lines = [line.rstrip() for line in stderr.splitlines() if line.strip()]
    tail = "\n".join(lines[-_STDERR_LINES:])[-_STDERR_CHARS:]
    if not tail:
        return f"pg_dump exited {code} without writing anything to stderr"
    message = f"pg_dump exited {code}: {tail}"
    if any(phrase in stderr.lower() for phrase in _SERVER_UNREACHABLE):
        message += "\nIs the database running? Start it with: fantabot-app db start"
    return message


def _scrub(text: str, database_url: str) -> str:
    """`text` with the DSN's password removed, in both of its spellings.

    The password reaches `pg_dump` through the environment so it stays out of `ps`; it must
    not come back out through an error message that gets pasted into a chat.
    """
    password = urlsplit(database_url.replace("+psycopg2://", "://", 1)).password
    if not password:
        return text
    for secret in {password, unquote(password)}:
        if secret:
            text = text.replace(secret, "***")
    return text


def run_dump(
    target: Path,
    database_url: str,
    *,
    run: Callable[[list[str], IO[bytes]], int | DumpExit] = _subprocess_pg_dump,
) -> DumpWrote:
    """Stream the database into `target`. The only thing here that touches the world.

    `target` is passed in rather than derived, so the path an operator was shown before
    the run is the path the run writes — deriving it twice is how a dump comes to be
    somewhere other than where the screen said it would be.

    Raises:
        PgDumpMissing: No `pg_dump` could be started.
        PgDumpFailed: It ran and exited non-zero; the message carries its stderr tail.
    """
    # The dump streams into a private sibling and replaces `target` only once whole. Writing
    # to `target` itself meant a failed second dump of the day truncated, then deleted, the
    # good one from the first, and two overlapping runs shared one half-written file.
    # Beside the target so `os.replace` stays on one filesystem and is atomic.
    descriptor, partial_name = tempfile.mkstemp(
        dir=target.parent, prefix=f"{target.name}.", suffix=".part"
    )
    partial = Path(partial_name)
    try:
        try:
            with os.fdopen(descriptor, "wb") as handle:
                outcome = run(pg_dump_argv(database_url), handle)
        except FileNotFoundError:
            raise PgDumpMissing(
                "pg_dump was not found: it is not on PATH, not beside the server recorded in "
                "~/.fantabot/pgdata/postmaster.opts, and not in the pixeltable_pgserver wheel. "
                "Provision the bundled server with `fantabot-app db start`, run fantabot from "
                "the app's environment, or install PostgreSQL client tools at least as new "
                "as the server."
            ) from None
        finished = outcome if isinstance(outcome, DumpExit) else DumpExit(code=outcome)
        if finished.code != 0:
            raise PgDumpFailed(
                failure_message(finished.code, _scrub(finished.stderr, database_url))
            )
        os.replace(partial, target)
    except BaseException:
        # One removal site for every way out, `KeyboardInterrupt` included: the app's stop
        # button reaches the child as `SIGINT` somewhere inside the stream.
        #
        # Removed rather than reported. A half-written dump is the right size to look real
        # and is refused by `pg_restore` only at the moment it is needed — when the disk it
        # was protecting against is already gone. Only the private partial is removed, so an
        # earlier good dump at `target` survives.
        partial.unlink(missing_ok=True)
        raise
    return DumpWrote(path=target, size_bytes=target.stat().st_size)
