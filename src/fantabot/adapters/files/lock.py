"""One collector and one loader per landing zone, enforced by the operating system.

**Two roles, not one.** Two collectors against the same landing zone double every record;
two loaders corrupt each other's ladders. Collector-plus-loader is the *intended* pairing —
the collector appends and the loader reads behind it — so a single mutex per file would ban
the normal case to prevent the abnormal ones.

**An advisory lock, not a pid file.** The property being bought is that the kernel releases
the lock however the holder dies: `SIGKILL`, a panic, the machine losing power. A pid file
records an intention and outlives the process that wrote it, so "is a collector running?"
degrades into "is pid 40122 still a collector, or is it now a browser?" — a question with
no answer, asked at 21:47 on an asta evening. Held this way, "is a collector running?" is
answered by trying to take the lock.

**No database, ever.** This module is on the collection path
(`tests/application/test_aste_outage.py`), so it may not reach persistence or SQLAlchemy —
a lock that can block on Postgres would hand the database the power to stop collection that
the landing zone exists to deny it.

The Windows backend is written and **unverified on this machine**; see `_lock_windows`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

#: The collector: subscribes to live auctions and appends to the landing zone.
COLLECTOR = "collector"
#: The loader: reads the landing zone and carries it into Postgres.
LOADER = "loader"

ROLES = (COLLECTOR, LOADER)


class RoleBusy(RuntimeError):
    """Another process already holds this role for this landing zone.

    Carries the role and the lock file rather than only a message, because the caller —
    `harvest collect`, and later the app's supervisor — has to report *which* of the two is
    running and *where*, and re-parsing a sentence for that is how the two drift apart.
    """

    def __init__(self, role: str, path: Path) -> None:
        super().__init__(
            f"another {role} already holds {path.parent / path.name.removesuffix(f'.{role}.lock')}"
            f" (lock: {path}). Stop it before starting a second one."
        )
        self.role = role
        self.path = path


def lock_path(landing: Path, role: str) -> Path:
    """`<landing>.<role>.lock` — beside the file it protects, and named for the role.

    Beside it so `ls` in the harvest home answers "what is running here", and derived from
    the landing zone rather than from a fixed location so two landing zones never share a
    lock.
    """
    if role not in ROLES:
        raise ValueError(f"{role!r} is not a role. Use one of: {', '.join(ROLES)}")
    return landing.with_name(f"{landing.name}.{role}.lock")


def _lock_posix(handle: IO[bytes], path: Path, role: str) -> None:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise RoleBusy(role, path) from exc


def _lock_windows(handle: IO[bytes], path: Path, role: str) -> None:  # pragma: no cover
    """`msvcrt.locking(LK_NBLCK)` over the first byte.

    **Unverified on this machine** — written from the documented contract, not observed.
    The app targets Windows and the property that makes this worth having (the OS releases
    the lock however the holder dies) holds there too, so a POSIX-only lock would have been
    a POSIX-only guarantee. Untested until T42c, whose path filter finally makes a change
    to this file trigger the one Windows runner that exercises it.

    A byte is written first because `msvcrt.locking` locks a *range*, and a range of an
    empty file is not lockable — unlike `flock`, which locks the open file description.
    """
    import msvcrt

    try:
        handle.write(b"\0")
        handle.flush()
        handle.seek(0)
        # Ignored, not narrowed: `msvcrt` exists on every platform as a stub with no
        # members off Windows, so mypy run on macOS cannot see `locking` at all. A
        # `sys.platform` guard would silence it by making this body unreachable — and an
        # unchecked body is exactly what "unverified on this machine" must not also mean.
        msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(), msvcrt.LK_NBLCK, 1  # type: ignore[attr-defined]
        )
    except OSError as exc:
        raise RoleBusy(role, path) from exc


@contextmanager
def role_lock(landing: Path, role: str) -> Iterator[Path]:
    """Hold `role` for `landing` for the duration of the block, or raise `RoleBusy`.

    Yields the lock file's path, so a caller that wants to say where it is does not have to
    recompute it.

    The handle is closed on the way out, which is what releases the lock; the file itself is
    left behind deliberately. Unlinking it would open the window every lockfile-by-existence
    scheme has — a second process opening the path between the unlink and its own create,
    and both believing they hold it. An empty `.lock` file that nobody holds costs nothing.
    """
    path = lock_path(landing, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    # "a+b", not "w": truncating is a write, and a second process must not be able to
    # disturb the holder's file even by zero bytes.
    handle = path.open("a+b")
    try:
        if os.name == "nt":  # pragma: no cover - POSIX here
            _lock_windows(handle, path, role)
        else:
            _lock_posix(handle, path, role)
    except BaseException:
        handle.close()
        raise
    try:
        yield path
    finally:
        handle.close()
