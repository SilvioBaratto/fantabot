"""Moving the harvest artefacts into ``~/.fantabot/aste_live``, or refusing to.

``live.jsonl``, its ``.offset`` and its ``.state`` describe **one position in one file**.
Separate them and the loader either re-reads 1.31 GB (the offset lost) or rebuilds every
ladder from nothing (the state lost) — both of the failures the 2026-09-05 Classic
recovery was made of. So this moves them together or it moves nothing.

**Copy, verify, then remove.** The repository and the home are routinely on different
volumes, so a rename is not available and the copy is real — 1.4 GB of it. A run killed
halfway therefore leaves every *source* file intact and some duplicated at the
destination, which the next run (idempotent by size) finishes. The opposite order would
make a kill during the move indistinguishable from a successful one.

Nothing here decides *where* the home is: that is `fantabot.config.harvest_dir`, one
definition shared with the CLI whose files these are.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fantabot.config import harvest_dir

__all__ = ["AdoptRefused", "Adopted", "adopt", "harvest_dir"]

#: Sidecars that are meaningless without the file they are named after. Both are written
#: by `harvest load` and both are a position in `live.jsonl`.
SIDECARS = (".offset", ".state")


class AdoptRefused(Exception):
    """The move was not attempted, or was stopped before anything was removed.

    Always raised *before* a source file is unlinked, so the operator's own copy is
    still whole whatever the message says.
    """


@dataclass(frozen=True)
class Adopted:
    """What one run did. `skipped` is a file already at the destination at the same size."""

    moved: int
    skipped: int
    total_bytes: int
    destination: Path


def _same_directory(source: Path, destination: Path) -> bool:
    """Whether two paths name one directory — by identity, not by spelling.

    `./data/aste_live`, an absolute path to it, and a symlink pointing at it are three
    `Path` objects that compare unequal and are the same directory. `Path.samefile` asks
    the filesystem (device and inode), which is the only comparison that survives a
    symlink, a relative path, a trailing slash and a case-insensitive volume.
    """
    try:
        return source.resolve() == destination.resolve() or source.samefile(destination)
    except OSError:
        # The destination does not exist yet, which is the ordinary first-run case.
        return False


def _free_bytes(path: Path) -> int:
    """Free space on the volume that will hold `path`, walking up to a directory that exists."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _offset_of(path: Path) -> int | None:
    """The integer in an `.offset` file, or `None` when it does not hold one.

    Unreadable is not a refusal: `harvest load` treats a torn checkpoint as "start from
    zero", and this command is not the place to be stricter than the reader is.
    """
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _check(source: Path, destination: Path, names: list[str], total: int, free: int) -> None:
    """Every reason to refuse, all of them before the first byte is copied."""
    if free < total:
        raise AdoptRefused(
            f"{destination} has {free} bytes free and this move needs {total}. "
            f"Nothing was moved. Either free space, or leave the artefacts where they are "
            f"and point the CLI at them by exporting "
            # Absolute, and no full stop after it: the whole defect being removed here is a
            # harvest path that resolves against a working directory, and the operator is
            # going to copy this line into a shell.
            f"FANTABOT_HARVEST_DIR={source.resolve()}"
        )
    present = set(names)
    for name in names:
        for suffix in SIDECARS:
            if name.endswith(suffix) and name[: -len(suffix)] not in present:
                raise AdoptRefused(
                    f"{name} is a position in {name[: -len(suffix)]}, which is not here. "
                    f"Moving it alone would describe a file that does not exist. "
                    f"Nothing was moved."
                )
        if not name.endswith(".offset"):
            continue
        offset = _offset_of(source / name)
        size = (source / name[: -len(".offset")]).stat().st_size
        if offset is not None and offset > size:
            raise AdoptRefused(
                f"{name} holds {offset}, past the end of its {size}-byte file. The two do "
                f"not describe the same landing zone; loading from it would skip every "
                f"record after {size}. Nothing was moved."
            )


def _verify(destination: Path, names: list[str]) -> None:
    """The same offset check, on what actually landed.

    Run again rather than trusted, because the point of a copy is that it can be short: a
    truncated `live.jsonl` beside a whole `.offset` is exactly the state that makes the
    loader skip an evening, and it is invisible until records go missing.
    """
    for name in names:
        if not name.endswith(".offset"):
            continue
        offset = _offset_of(destination / name)
        landed = destination / name[: -len(".offset")]
        if offset is not None and landed.exists() and offset > landed.stat().st_size:
            raise AdoptRefused(
                f"after the move {name} holds {offset}, past the end of the "
                f"{landed.stat().st_size}-byte file that arrived. The copy is short — the "
                f"source files were left in place; re-run once there is room."
            )


def adopt(
    source: Path,
    destination: Path | None = None,
    *,
    disk_free: Callable[[Path], int] = _free_bytes,
) -> Adopted:
    """Move every file under `source` into the harvest home, or refuse and move nothing.

    `disk_free` is injected so the free-space refusal can be tested without filling a
    disk — the same seam `PostgresProvisioner` uses for its postmaster.
    """
    destination = destination if destination is not None else harvest_dir()
    if not source.is_dir():
        return Adopted(moved=0, skipped=0, total_bytes=0, destination=destination)
    if _same_directory(source, destination):
        # Nothing to do, and saying so is not a nicety — it is the difference between this
        # and deleting the landing zone. Every file would otherwise be "already at the
        # destination at the same size", which the idempotency branch below reads as the
        # previous run's own work and finishes by unlinking the source. The source *is* the
        # destination. An operator whose home volume is too small sets FANTABOT_HARVEST_DIR
        # to the directory the artefacts are already in, and `adopt`'s own `--from` default
        # names that same directory: the arrangement is reached by following the advice.
        return Adopted(moved=0, skipped=0, total_bytes=0, destination=destination)

    entries = sorted(p for p in source.iterdir() if p.is_file())
    names = [p.name for p in entries]

    skipped: list[Path] = []
    to_move: list[Path] = []
    for entry in entries:
        landed = destination / entry.name
        if not landed.exists():
            to_move.append(entry)
        elif landed.stat().st_size == entry.stat().st_size:
            # The previous run's own work. Removing the source is what finishes it.
            skipped.append(entry)
        else:
            raise AdoptRefused(
                f"{landed} already exists at a different size "
                f"({landed.stat().st_size} vs {entry.stat().st_size} bytes). This would "
                f"overwrite a landing zone that is not the one being adopted. Nothing "
                f"was moved."
            )

    total = sum(p.stat().st_size for p in to_move)
    _check(source, destination, names, total, disk_free(destination))
    if not to_move and not skipped:
        return Adopted(moved=0, skipped=0, total_bytes=0, destination=destination)

    destination.mkdir(parents=True, exist_ok=True)
    for entry in to_move:
        shutil.copy2(entry, destination / entry.name)
    _verify(destination, names)
    for entry in to_move + skipped:
        entry.unlink()
    return Adopted(
        moved=len(to_move), skipped=len(skipped), total_bytes=total, destination=destination
    )
