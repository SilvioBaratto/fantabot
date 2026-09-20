"""Environment checks for ``fantabot-app doctor`` - the first thing to run when stuck.

Each check answers one question and never raises: a failure is reported, not thrown, so
``doctor`` always prints a full report. Checks read the real environment (interpreter,
imports, provisioned data dir); they open no sockets.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    return Check("python", version >= (3, 11), text)


def _fantabot() -> Check:
    try:
        import fantabot  # noqa: F401

        return Check("fantabot", True, "importable")
    except Exception as exc:  # noqa: BLE001
        return Check("fantabot", False, f"not importable: {type(exc).__name__}")


def _tree_app_package(library_init: Path) -> Path | None:
    """`<root>/app/fantabot_app`, when the library was imported from `<root>/src`.

    The editable `fantabot` is the only anchor available. A frozen `fantabot_app` knows
    nothing about where it was built from — but `tool.uv.sources` declares
    `fantabot = { path = "..", editable = true }`, so whenever the app half is installed
    from a source tree the library half points straight back at that tree. That
    asymmetry is what caused the defect and is also what makes it detectable.

    `pyproject.toml` is required as well as the package directory: `src/` and `app/`
    beside each other is a common enough shape that finding one alone would be a guess.
    """
    root = library_init.resolve().parents[2]
    candidate = root / "app" / "fantabot_app"
    if (candidate / "__init__.py").exists() and (root / "app" / "pyproject.toml").exists():
        return candidate
    return None


def compare_app_source(imported: Path, library_init: Path) -> Check:
    """Is the `fantabot_app` that is running the one in the source tree beside it?

    Pure, and taking both paths, so the three states are testable without installing
    anything. They are kept apart because their remedies are:

    * **No source tree** — a plain wheel install of both halves. Nothing is wrong, and a
      red mark here would be one every GitHub user sees on a correct setup, which is the
      fastest way to teach someone to ignore the report.
    * **In step** — editable, or running from the tree itself.
    * **A frozen copy beside a tree** — the defect this exists for, measured 2026-09-20:
      `uv tool install ./app` installs the app half by value while the library half is
      editable, so `fantabot-app` on PATH was missing `schedule` and `harvest` entirely
      and every documented line using them failed. Nothing reported it, for weeks.

    `samefile` rather than `==`, which is `CLAUDE.md`'s recorded lesson from `harvest
    adopt`: it deleted the landing zone it was asked to adopt by comparing two `Path`s
    that named one directory.
    """
    tree = _tree_app_package(library_init)
    if tree is None:
        return Check("fantabot-app", True, "installed (not from a source tree)")
    try:
        if imported.resolve().samefile(tree):
            return Check("fantabot-app", True, f"in step with {tree}")
    except OSError:
        # One of the two went away between the import and now. That is not a frozen
        # copy, and calling it one would send the operator to reinstall over a race.
        return Check("fantabot-app", True, "installed (source tree unreadable)")
    return Check(
        "fantabot-app",
        False,
        # Both paths, on their own lines, for `schedule status`'s MOVED reason: the
        # question is which of two copies is running, and one path is half an answer.
        "running a frozen copy\n"
        f"         imported {imported}\n"
        f"         tree     {tree}\n"
        "         fix: uv tool install --force --editable ./app",
    )


def _fantabot_app() -> Check:
    """The thin shell: resolve the two paths, then let the pure comparison decide."""
    try:
        import fantabot

        import fantabot_app
    except Exception as exc:  # noqa: BLE001
        return Check("fantabot-app", False, f"not importable: {type(exc).__name__}")
    if fantabot.__file__ is None or fantabot_app.__file__ is None:
        # A namespace package has no `__file__`, and there is nothing to compare.
        return Check("fantabot-app", True, "installed (no file to compare)")
    return compare_app_source(Path(fantabot_app.__file__).parent, Path(fantabot.__file__))


def _postgres_wheel() -> Check:
    try:
        import pixeltable_pgserver  # noqa: F401

        return Check("postgres", True, "pixeltable_pgserver wheel present")
    except Exception:  # noqa: BLE001
        return Check("postgres", False, "wheel missing - reinstall the app")


def _database() -> Check:
    from fantabot_app import paths

    if os.environ.get("FANTABOT_DATABASE_URL"):
        return Check("database", True, "FANTABOT_DATABASE_URL set")
    pgdata = paths.pgdata()
    if pgdata.exists():
        return Check("database", True, f"provisioned at {pgdata}")
    return Check("database", False, "not provisioned - run `fantabot-app setup`")


def _chromium() -> Check:
    try:
        import playwright  # noqa: F401

        return Check("chromium", True, "playwright present (run `setup` if login fails)")
    except Exception:  # noqa: BLE001
        return Check("chromium", False, "playwright missing - run `fantabot-app setup`")


def _encryption_key() -> Check:
    from fantabot_app import keyfile

    if os.environ.get(keyfile.ENV_ENCRYPTION_KEY) or keyfile.key_path().exists():
        return Check("encryption key", True, "present")  # never report the key itself
    return Check("encryption key", False, "not set - run `fantabot-app setup`")


def run_checks() -> list[Check]:
    """Run every check and return the results, in report order."""
    return [
        _python(),
        _fantabot(),
        # Straight after `fantabot`, because it is the same question one level up: that
        # one says the library imports, this one says whether the app beside it is the
        # code you are editing.
        _fantabot_app(),
        _postgres_wheel(),
        _database(),
        _encryption_key(),
        _chromium(),
    ]
