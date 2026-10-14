"""Which `pg_dump` runs, and what a non-zero exit is allowed to claim (2026-10-14).

`fantabot db dump` failed on this machine with `server version: 18.4; pg_dump version:
17.5`. The command resolved `pg_dump` on PATH, which here is Homebrew's 17.5, while the
bundled server is 18.4 — and `pg_dump` dumps an older or equal server and refuses a newer
one. The 18.4 binary was on disk the whole time, beside the `postgres` it ships with.

The failure message misdirected as well: every non-zero exit said "is the database
running?", and the database was running. So both halves are pinned here — the choice of
binary, and the refusal to name a cause the exit did not show.

Nothing in this file spawns a process. The choice is a function of three injected facts
(the bundled install, the PATH hit, and a version probe), and the real seam is exercised
with `subprocess.run` replaced.
"""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import IO

import pytest

from fantabot.application import db_dump
from fantabot.application.db_dump import (
    DumpExit,
    PgDumpBinary,
    PgDumpFailed,
    PgDumpMissing,
    parse_pg_dump_major,
    resolve_pg_dump,
    run_dump,
)

SOCKET = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"
MISMATCH = (
    "pg_dump: error: aborting because of server version mismatch\n"
    "pg_dump: detail: server version: 18.4; pg_dump version: 17.5\n"
)


class TestReadingAVersion:
    """The probe's banner reduced to the one number the choice depends on."""

    @pytest.mark.parametrize(
        ("banner", "major"),
        [
            ("pg_dump (PostgreSQL) 17.5", 17),
            ("pg_dump (PostgreSQL) 18.4 (Homebrew)\n", 18),
            ("pg_dump (PostgreSQL) 19devel", 19),
            ("pg_dump (PostgreSQL) 16beta2", 16),
            ("pg_dump (PostgreSQL) 9.6.24", 9),
        ],
    )
    def test_the_major_is_the_number_after_the_product_name(self, banner: str, major: int) -> None:
        """Release, devel, beta and Homebrew-suffixed banners all name the same field."""
        assert parse_pg_dump_major(banner) == major

    @pytest.mark.parametrize("banner", ["", "command not found", "pg_dump (PostgreSQL)", None])
    def test_a_banner_that_names_no_version_reads_as_unknown(self, banner: str | None) -> None:
        """Unknown, not zero: a zero would rank below every real version and lose a
        tie it never took part in."""
        assert parse_pg_dump_major(banner) is None


def _bundled(path: str = "/pg/pginstall18/bin/pg_dump", major: int = 18) -> Callable[[], tuple[str, int]]:
    return lambda: (path, major)


def _no_bundled() -> None:
    return None


class TestChoosingTheBinary:
    """A newer `pg_dump` dumps an older server; an older one refuses a newer server. So the
    only safe order is by version, with the bundled one winning a tie because it is the one
    guaranteed to match the server the app provisioned."""

    def test_the_bundled_18_beats_homebrews_17_which_is_the_failure_that_happened(self) -> None:
        """The measured case of 2026-10-14: PATH had 17.5, the server is 18.4."""
        chosen = resolve_pg_dump(
            bundled=_bundled(),
            on_path=lambda: "/opt/homebrew/bin/pg_dump",
            version_of=lambda path: 17,
        )

        assert chosen == PgDumpBinary(path="/pg/pginstall18/bin/pg_dump", major=18, source="bundled")

    def test_a_newer_client_on_path_beats_an_older_bundled_one(self) -> None:
        """A server-side upgrade must not be pinned to the wheel's older client."""
        chosen = resolve_pg_dump(
            bundled=_bundled("/pg/pginstall/bin/pg_dump", 16),
            on_path=lambda: "/usr/bin/pg_dump",
            version_of=lambda path: 18,
        )

        assert chosen == PgDumpBinary(path="/usr/bin/pg_dump", major=18, source="PATH")

    def test_a_tie_goes_to_the_bundled_one(self) -> None:
        """Equal majors: the bundled client is the one built with the server it dumps."""
        chosen = resolve_pg_dump(
            bundled=_bundled(),
            on_path=lambda: "/usr/bin/pg_dump",
            version_of=lambda path: 18,
        )

        assert chosen is not None
        assert chosen.source == "bundled"

    def test_only_path_is_used_when_the_bundled_install_is_absent(self) -> None:
        """A library install without the app has nothing bundled to prefer."""
        chosen = resolve_pg_dump(
            bundled=_no_bundled,
            on_path=lambda: "/usr/bin/pg_dump",
            version_of=lambda path: 17,
        )

        assert chosen == PgDumpBinary(path="/usr/bin/pg_dump", major=17, source="PATH")

    def test_only_the_bundled_one_is_used_when_nothing_is_on_path(self) -> None:
        """No PATH hit means no probe and no contest."""
        chosen = resolve_pg_dump(bundled=_bundled(), on_path=lambda: None, version_of=lambda p: 1)

        assert chosen == PgDumpBinary(path="/pg/pginstall18/bin/pg_dump", major=18, source="bundled")

    def test_a_path_binary_that_will_not_say_its_version_loses_to_the_bundled_one(self) -> None:
        """Guessing it is new enough is how a client older than the server gets picked."""
        chosen = resolve_pg_dump(
            bundled=_bundled(),
            on_path=lambda: "/usr/bin/pg_dump",
            version_of=lambda path: None,
        )

        assert chosen is not None
        assert chosen.source == "bundled"

    def test_a_path_binary_of_unknown_version_is_still_better_than_nothing(self) -> None:
        """With no bundled install, an unreadable banner must not turn into "missing"."""
        chosen = resolve_pg_dump(
            bundled=_no_bundled, on_path=lambda: "/usr/bin/pg_dump", version_of=lambda p: None
        )

        assert chosen == PgDumpBinary(path="/usr/bin/pg_dump", major=None, source="PATH")

    def test_nothing_anywhere_is_none_not_a_made_up_name(self) -> None:
        """The caller keeps its own `PgDumpMissing` for this; a guessed name would hide it."""
        assert resolve_pg_dump(bundled=_no_bundled, on_path=lambda: None) is None

    def test_the_bundled_version_is_not_probed(self) -> None:
        """Its major is the key it is filed under; spawning it to learn what it already
        said is a process per dump for nothing."""
        probed: list[str] = []

        def _probe(path: str) -> int | None:
            probed.append(path)
            return 17

        resolve_pg_dump(bundled=_bundled(), on_path=lambda: "/usr/bin/pg_dump", version_of=_probe)

        assert probed == ["/usr/bin/pg_dump"]


def _fake_pgserver(monkeypatch: pytest.MonkeyPatch, versions: dict[int, Path]) -> None:
    package = types.ModuleType("pixeltable_pgserver")
    utils = types.ModuleType("pixeltable_pgserver.utils")
    utils.POSTGRES_VERSIONS = versions  # type: ignore[attr-defined]
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pixeltable_pgserver", package)
    monkeypatch.setitem(sys.modules, "pixeltable_pgserver.utils", utils)


def _install(directory: Path) -> Path:
    """A `bin/` directory holding an executable `pg_dump`, whatever the platform calls it."""
    directory.mkdir(parents=True)
    name = "pg_dump.exe" if sys.platform == "win32" else "pg_dump"
    binary = directory / name
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


class TestTheWheelsInstall:
    """The wheel lookup against a stand-in `pixeltable_pgserver`, never the installed one."""

    def test_the_highest_version_that_has_a_pg_dump_is_the_bundled_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two installs side by side: the 18 wins."""
        old = _install(tmp_path / "pginstall" / "bin")
        new = _install(tmp_path / "pginstall18" / "bin")
        _fake_pgserver(monkeypatch, {16: old.parent, 18: new.parent})

        found = db_dump._wheel_pg_dump()

        assert found is not None
        assert Path(found[0]) == new
        assert found[1] == 18

    def test_a_newer_version_with_no_pg_dump_in_it_falls_back_to_the_next(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A server-only install lists its version and ships no client tools."""
        old = _install(tmp_path / "pginstall" / "bin")
        (tmp_path / "pginstall18" / "bin").mkdir(parents=True)
        _fake_pgserver(monkeypatch, {16: old.parent, 18: tmp_path / "pginstall18" / "bin"})

        found = db_dump._wheel_pg_dump()

        assert found is not None
        assert found[1] == 16

    def test_a_library_without_the_server_wheel_imports_and_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`fantabot` is installable without the app, so the wheel is an optional hit."""
        # Both entries: a test that ran earlier may already have the real `.utils` cached,
        # and `from pkg.utils import x` consults that entry before the parent's.
        monkeypatch.setitem(sys.modules, "pixeltable_pgserver", None)
        monkeypatch.setitem(sys.modules, "pixeltable_pgserver.utils", None)

        assert db_dump._wheel_pg_dump() is None

    def test_an_empty_version_table_reports_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A wheel that lists no installs is the same answer as no wheel."""
        _fake_pgserver(monkeypatch, {})

        assert db_dump._wheel_pg_dump() is None

    def test_a_wheel_without_the_version_table_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A release that renamed `POSTGRES_VERSIONS` is an `ImportError` on the name, which
        is not a `ModuleNotFoundError`: the handler has to be the wider of the two."""
        package = types.ModuleType("pixeltable_pgserver")
        utils = types.ModuleType("pixeltable_pgserver.utils")
        package.utils = utils  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pixeltable_pgserver", package)
        monkeypatch.setitem(sys.modules, "pixeltable_pgserver.utils", utils)

        assert db_dump._wheel_pg_dump() is None

    def test_a_wheel_that_refuses_the_configured_version_reports_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`pixeltable_pgserver.utils` raises `RuntimeError` at import when
        `PGSERVER_POSTGRES_VERSION` names a major it does not ship. That is not a reason for
        `fantabot db dump` to end in a traceback; PATH is still a candidate."""
        package = tmp_path / "pixeltable_pgserver"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "utils.py").write_text(
            "raise RuntimeError('Unsupported Postgres version: 17')\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        # Set then deleted, so teardown restores whatever was cached before: a failed import
        # of `.utils` leaves its parent package behind in `sys.modules` for later tests.
        for name in ("pixeltable_pgserver", "pixeltable_pgserver.utils"):
            monkeypatch.setitem(sys.modules, name, None)
            monkeypatch.delitem(sys.modules, name)

        assert db_dump._wheel_pg_dump() is None


POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-in binaries are shell scripts"
)


def _provision(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    major: str = "18",
    install: str = "app env/pginstall18/bin",
    opts: str | None = None,
    with_pg_dump: bool = True,
) -> Path:
    """A `~/.fantabot/pgdata` as the bundled server leaves it, under a redirected home.

    The install directory holds a space by default, because the real one is under a volume
    named `External SSD`. Returns the install's `bin` directory.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    pgdata = home / ".fantabot" / "pgdata"
    pgdata.mkdir(parents=True)
    bin_dir = home / install
    if with_pg_dump:
        _install(bin_dir)
    else:
        bin_dir.mkdir(parents=True)
    (bin_dir / "postgres").write_text("#!/bin/sh\n")
    (pgdata / "PG_VERSION").write_text(f"{major}\n")
    # What `postmaster.opts` really holds: the executable unquoted, every argument quoted.
    written = opts if opts is not None else (
        f'{bin_dir / "postgres"} "-D" "{pgdata}" "-h" "" "-k" "{pgdata}"\n'
    )
    (pgdata / "postmaster.opts").write_text(written)
    return bin_dir


class TestTheRunningServersClient:
    """The bundled client is found where the *server* says it is, with no wheel to import.

    The wheel belongs to the app. The `fantabot` an operator types into is another
    interpreter, and a lookup that needed the import chose PATH's older client in exactly
    that one.
    """

    @POSIX_ONLY
    def test_the_pg_dump_beside_the_recorded_postgres_is_found_through_a_path_with_a_space(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The executable is everything before the first quoted argument, not the first word."""
        bin_dir = _provision(tmp_path, monkeypatch)

        found = db_dump._server_pg_dump()

        assert found is not None
        assert Path(found[0]) == bin_dir / "pg_dump"
        assert found[1] == 18

    @POSIX_ONLY
    def test_the_major_is_the_data_directorys_own_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read off `PG_VERSION`, which an old server writes as `9.6`."""
        _provision(tmp_path, monkeypatch, major="9.6")

        found = db_dump._server_pg_dump()

        assert found is not None
        assert found[1] == 9

    def test_a_server_never_started_has_no_record_to_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No `postmaster.opts` is an absence, not an error; the wheel is asked next."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        assert db_dump._server_pg_dump() is None

    @POSIX_ONLY
    @pytest.mark.parametrize("opts", ["", "   \n"])
    def test_an_empty_record_names_no_executable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opts: str
    ) -> None:
        """A torn write of the file reads as nothing rather than as the current directory."""
        _provision(tmp_path, monkeypatch, opts=opts)

        assert db_dump._server_pg_dump() is None

    @POSIX_ONLY
    def test_an_unreadable_version_reads_as_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a major the candidate cannot be ranked, so it is not offered."""
        _provision(tmp_path, monkeypatch, major="not-a-number")

        assert db_dump._server_pg_dump() is None

    @POSIX_ONLY
    def test_an_install_that_has_moved_since_the_server_started_reads_as_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`postmaster.opts` outlives the server and the venv it names: a rebuilt
        environment leaves a record of a directory that is gone."""
        _provision(tmp_path, monkeypatch, with_pg_dump=False)

        assert db_dump._server_pg_dump() is None

    @POSIX_ONLY
    def test_it_is_tried_before_the_wheel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The binary of the server being dumped outranks a table of what the wheel ships."""
        bin_dir = _provision(tmp_path, monkeypatch)
        wheel = _install(tmp_path / "wheel" / "bin")
        _fake_pgserver(monkeypatch, {18: wheel.parent})

        found = db_dump._bundled_pg_dump()

        assert found is not None
        assert Path(found[0]) == bin_dir / "pg_dump"

    def test_the_wheel_answers_when_the_server_has_left_no_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback: a freshly installed app whose server has not been started."""
        monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty-home"))
        wheel = _install(tmp_path / "wheel" / "bin")
        _fake_pgserver(monkeypatch, {18: wheel.parent})

        found = db_dump._bundled_pg_dump()

        assert found is not None
        assert Path(found[0]) == wheel
        assert found[1] == 18


def _fake_binary(directory: Path, banner: str) -> Path:
    """An executable `pg_dump` that prints `banner` on stdout, as the real one does."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "pg_dump"
    binary.write_text(f"#!/bin/sh\necho '{banner}'\n")
    binary.chmod(0o755)
    return binary


@POSIX_ONLY
class TestResolvingWithNothingInjected:
    """`resolve_pg_dump()` as the CLI calls it: every seam real, the machine stood in for.

    Every other test in this file hands the resolver its candidates, so none could say that
    the defaults are wired to anything, or that the operator's interpreter — which cannot
    import the wheel — still lands on the bundled client.
    """

    def _machine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, on_path: str | None
    ) -> Path:
        """A home with a started 18 server, no importable wheel, and `on_path` on PATH."""
        bin_dir = _provision(tmp_path / "home", monkeypatch)
        monkeypatch.setitem(sys.modules, "pixeltable_pgserver", None)
        monkeypatch.setitem(sys.modules, "pixeltable_pgserver.utils", None)
        if on_path is None:
            monkeypatch.setenv("PATH", str(tmp_path / "no-such-dir"))
        else:
            _fake_binary(tmp_path / "pathbin", on_path)
            monkeypatch.setenv("PATH", str(tmp_path / "pathbin"))
        return bin_dir

    def test_the_operators_interpreter_gets_the_bundled_18_over_a_17_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure that was reported, in the interpreter where the wheel is absent."""
        bin_dir = self._machine(tmp_path, monkeypatch, on_path="pg_dump (PostgreSQL) 17.5")

        chosen = resolve_pg_dump()

        assert chosen is not None
        assert chosen.source == "bundled"
        assert chosen.major == 18
        assert Path(chosen.path) == bin_dir / "pg_dump"

    def test_a_newer_client_on_path_is_found_and_probed_through_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PATH is consulted, its banner is read from where `pg_dump` prints it, and a
        higher major wins."""
        self._machine(tmp_path, monkeypatch, on_path="pg_dump (PostgreSQL) 19.0")

        chosen = resolve_pg_dump()

        assert chosen == PgDumpBinary(
            path=str(tmp_path / "pathbin" / "pg_dump"), major=19, source="PATH"
        )

    def test_nothing_on_path_leaves_the_bundled_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty PATH is no contest."""
        self._machine(tmp_path, monkeypatch, on_path=None)

        chosen = resolve_pg_dump()

        assert chosen is not None
        assert chosen.source == "bundled"

    def test_a_probe_that_cannot_run_the_binary_reads_as_unknown(self, tmp_path: Path) -> None:
        """A PATH entry that is not executable must not raise out of the resolver."""
        assert db_dump._probe_major(str(tmp_path / "missing" / "pg_dump")) is None

    def test_a_banner_on_stderr_is_not_a_banner(self, tmp_path: Path) -> None:
        """`--version` prints on stdout. A wrapper that complains on stderr and prints
        nothing else has said nothing about its version."""
        binary = tmp_path / "pg_dump"
        binary.write_text("#!/bin/sh\necho 'pg_dump (PostgreSQL) 19.0' >&2\n")
        binary.chmod(0o755)

        assert db_dump._probe_major(str(binary)) is None


def _capture_run(
    monkeypatch: pytest.MonkeyPatch, *, code: int = 0, stderr: bytes = b""
) -> list[list[str]]:
    seen: list[list[str]] = []

    def _fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, code, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return seen


class TestTheRealSeamUsesTheChoice:
    """`_subprocess_pg_dump` is where the choice becomes an argv; `subprocess.run` is faked."""

    def test_the_resolved_binary_replaces_the_bare_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """argv[0] is the chosen path and the rest of the argv is untouched."""
        chosen = PgDumpBinary(path="/pg/pginstall18/bin/pg_dump", major=18, source="bundled")
        monkeypatch.setattr(db_dump, "resolve_pg_dump", lambda: chosen)
        seen = _capture_run(monkeypatch)

        with (tmp_path / "x").open("wb") as handle:
            db_dump._subprocess_pg_dump(db_dump.pg_dump_argv(SOCKET), handle)

        assert seen[-1][0] == "/pg/pginstall18/bin/pg_dump"
        assert seen[-1][1] == "-Fc"

    def test_nothing_found_keeps_the_bare_name_so_the_missing_outcome_still_fires(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawning the bare name is what raises `FileNotFoundError`, which is what
        `run_dump` turns into `PgDumpMissing`."""
        monkeypatch.setattr(db_dump, "resolve_pg_dump", lambda: None)
        seen = _capture_run(monkeypatch)

        with (tmp_path / "x").open("wb") as handle:
            db_dump._subprocess_pg_dump(db_dump.pg_dump_argv(SOCKET), handle)

        assert seen[-1][0] == "pg_dump"

    def test_stderr_is_requested_as_a_pipe_and_stdout_goes_to_the_dump_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two streams are routed differently: the dump streams to the file handle, and
        stderr is the only thing buffered. A stdout pipe would hold 2 GB in memory."""
        monkeypatch.setattr(db_dump, "resolve_pg_dump", lambda: None)
        seen: dict[str, object] = {}

        def _fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
            seen.update(kw)
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        with (tmp_path / "x").open("wb") as handle:
            db_dump._subprocess_pg_dump(db_dump.pg_dump_argv(SOCKET), handle)

        assert seen["stderr"] == subprocess.PIPE
        assert seen["stdout"] is handle

    def test_the_exit_carries_what_pg_dump_said_on_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stderr is captured so a failure can say what happened instead of guessing."""
        monkeypatch.setattr(db_dump, "resolve_pg_dump", lambda: None)
        _capture_run(monkeypatch, code=1, stderr=MISMATCH.encode())

        with (tmp_path / "x").open("wb") as handle:
            outcome = db_dump._subprocess_pg_dump(db_dump.pg_dump_argv(SOCKET), handle)

        assert outcome == DumpExit(code=1, stderr=MISMATCH)

    def test_stderr_on_a_successful_dump_still_reaches_the_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Piping stderr for the failure message must not swallow the warnings pg_dump
        prints on the runs that work."""
        monkeypatch.setattr(db_dump, "resolve_pg_dump", lambda: None)
        _capture_run(monkeypatch, code=0, stderr=b"pg_dump: warning: circular foreign keys\n")

        with (tmp_path / "x").open("wb") as handle:
            db_dump._subprocess_pg_dump(db_dump.pg_dump_argv(SOCKET), handle)

        assert "circular foreign keys" in capsys.readouterr().err

    def test_a_missing_binary_still_raises_file_not_found_through_the_seam(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end through the real seam: no binary is `PgDumpMissing`."""
        monkeypatch.setattr(db_dump, "resolve_pg_dump", lambda: None)

        def _absent(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
            raise FileNotFoundError(2, "No such file or directory", "pg_dump")

        monkeypatch.setattr(subprocess, "run", _absent)

        with pytest.raises(PgDumpMissing):
            run_dump(tmp_path / "d.dump", SOCKET)


def _exits(code: int, stderr: str) -> Callable[[list[str], IO[bytes]], DumpExit]:
    def _run(argv: list[str], stdout: IO[bytes]) -> DumpExit:
        return DumpExit(code=code, stderr=stderr)

    return _run


class TestWhatANonZeroExitClaims:
    """The message names a cause only when stderr showed one."""

    def test_a_version_mismatch_is_named_with_both_versions(self, tmp_path: Path) -> None:
        """The operator needs the two numbers to know which side to change."""
        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(1, MISMATCH))

        message = str(failed.value)
        assert "pg_dump 17.5 cannot dump a PostgreSQL 18.4 server" in message
        assert "version mismatch" in message
        assert "at least as new as the server" in message

    def test_a_version_mismatch_does_not_blame_the_database(self, tmp_path: Path) -> None:
        """The database was running. That sentence cost an investigation."""
        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(1, MISMATCH))

        assert "database running" not in str(failed.value)
        assert "db start" not in str(failed.value)

    def test_a_refused_connection_still_points_at_the_server(self, tmp_path: Path) -> None:
        """When stderr does say the server is unreachable, the old advice is right."""
        refused = (
            'pg_dump: error: connection to server on socket "/x/.s.PGSQL.5432" failed: '
            "No such file or directory\n\tIs the server running locally?\n"
        )

        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(1, refused))

        assert "fantabot-app db start" in str(failed.value)
        assert "No such file or directory" in str(failed.value)

    def test_a_cause_that_is_not_known_is_not_invented(self, tmp_path: Path) -> None:
        """A permission error is reported verbatim, with no advice about the server."""
        with pytest.raises(PgDumpFailed) as failed:
            run_dump(
                tmp_path / "d.dump",
                SOCKET,
                run=_exits(1, "pg_dump: error: query failed: ERROR:  permission denied\n"),
            )

        message = str(failed.value)
        assert "permission denied" in message
        assert "database running" not in message
        assert "db start" not in message

    def test_a_silent_exit_is_reported_as_silent_and_names_no_cause(self, tmp_path: Path) -> None:
        """No stderr is a fact worth stating, and not a licence to guess."""
        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(2, ""))

        message = str(failed.value)
        assert "exited 2" in message
        assert "database running" not in message

    def test_a_bare_integer_from_a_seam_still_means_an_exit_code(self, tmp_path: Path) -> None:
        """Every fake `run` written before the exit carried stderr returns a plain int."""

        def _old_style(argv: list[str], stdout: IO[bytes]) -> int:
            return 1

        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_old_style)

        assert "exited 1" in str(failed.value)

    def test_only_the_tail_of_a_long_stderr_is_kept(self, tmp_path: Path) -> None:
        """The cause is at the end; a verbose run must not turn the message into a log."""
        noisy = "".join(f"pg_dump: warning: line {n}\n" for n in range(200))

        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(1, noisy))

        message = str(failed.value)
        assert "line 199" in message
        assert "line 0\n" not in message
        assert len(message) < 2_500

    def test_the_dsn_password_never_reaches_the_message(self, tmp_path: Path) -> None:
        """Both spellings: the percent-encoded one in the DSN and the decoded one libpq uses."""
        dsn = "postgresql+psycopg2://postgres:p%40ssCanary@localhost:5432/fantabot"
        echoed = 'pg_dump: error: password "p@ssCanary" rejected, p%40ssCanary\n'

        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", dsn, run=_exits(1, echoed))

        assert "ssCanary" not in str(failed.value)

    def test_a_failed_dump_still_leaves_no_file_behind(self, tmp_path: Path) -> None:
        """The atomic-replace guarantee survives the richer exit."""
        target = tmp_path / "d.dump"

        def _half(argv: list[str], stdout: IO[bytes]) -> DumpExit:
            stdout.write(b"PGDMP" + b"x" * 50)
            return DumpExit(code=1, stderr=MISMATCH)

        with pytest.raises(PgDumpFailed):
            run_dump(target, SOCKET, run=_half)

        assert sorted(p.name for p in tmp_path.iterdir()) == []

    def test_a_distributor_banner_on_both_versions_still_names_the_numbers(
        self, tmp_path: Path
    ) -> None:
        """Debian and RPM builds append a banner with spaces to each version."""
        stderr = (
            "pg_dump: error: aborting because of server version mismatch\n"
            "pg_dump: detail: server version: 18.4 (Debian 18.4-1.pgdg120+1); "
            "pg_dump version: 17.5 (Debian 17.5-1.pgdg120+1)\n"
        )

        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(1, stderr))

        assert "pg_dump 17.5 cannot dump a PostgreSQL 18.4 server" in str(failed.value)

    def test_a_mismatch_without_the_detail_line_is_still_a_mismatch(self, tmp_path: Path) -> None:
        """The headline alone, as an older client prints it: no numbers to quote, and no
        advice about the server either."""
        with pytest.raises(PgDumpFailed) as failed:
            run_dump(
                tmp_path / "d.dump",
                SOCKET,
                run=_exits(1, "pg_dump: error: aborting because of server version mismatch\n"),
            )

        message = str(failed.value)
        assert "pg_dump refused this server (version mismatch)" in message
        assert "db start" not in message

    def test_the_remedy_does_not_name_a_wheel_directory_that_will_move(
        self, tmp_path: Path
    ) -> None:
        """The wheel's install directory carries the server's major in its name."""
        with pytest.raises(PgDumpFailed) as failed:
            run_dump(tmp_path / "d.dump", SOCKET, run=_exits(1, MISMATCH))

        assert "pginstall" not in str(failed.value)

    def test_a_missing_pg_dump_names_both_places_it_looked(self, tmp_path: Path) -> None:
        """The remedy depends on which place was empty, so the message lists both."""

        def _absent(argv: list[str], stdout: IO[bytes]) -> int:
            raise FileNotFoundError(2, "No such file or directory", "pg_dump")

        with pytest.raises(PgDumpMissing) as missing:
            run_dump(tmp_path / "d.dump", SOCKET, run=_absent)

        message = str(missing.value)
        assert "PATH" in message
        assert "postmaster.opts" in message
        assert "pixeltable_pgserver" in message
        assert "pginstall" not in message
