"""Environment-adaptive configuration loader.

Locates the project ``.env`` by walking up the directory tree and loads it into
``os.environ`` before any Settings instance is created. This is what lets the
app find the ROOT-level ``.env`` (one directory above ``api/``) regardless of the
current working directory — ``cd api && uvicorn`` and running from the project
root both resolve the same file. Where there is no ``.env`` at all — a wheel install, CI,
a shell that exports everything — the load step is simply skipped and real environment
variables are the whole configuration. (This paragraph said "in Docker" until 2026-09-24;
the compose stack is gone, as ``_ROOT_MARKERS`` below already records.)
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Marker of the PROJECT ROOT (where the .env lives). The walk stops here so the
# search never escapes into a parent repo or the filesystem root. `docker-compose.yml`
# was the other marker until the compose stack was deleted; `.git` is the one left.
_ROOT_MARKERS = (".git",)


def _walk_up_for_dotenv(origin: Path) -> Path | None:
    """Ascend from *origin*, returning the first ``.env`` found.

    Stops at a project-root marker (`.git`) so the search
    never escapes past the project root.
    """
    for directory in (origin, *origin.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
        if any((directory / marker).exists() for marker in _ROOT_MARKERS):
            return None
    return None


def find_dotenv(start: Path | None = None) -> Path | None:
    """Locate the project ``.env`` by walking up from *start*.

    Searches upward first from the current working directory (covers ``cd api &&
    uvicorn`` and running from the project root), then from this module's
    location (covers imports from odd CWDs / installed packages). Returns the
    first ``.env`` found, or ``None`` — callers then rely on real environment
    variables (12-factor).
    """
    search_origins = []
    if start is not None:
        search_origins.append(start.resolve())
    else:
        search_origins.append(Path.cwd())
        search_origins.append(Path(__file__).resolve())

    for origin in search_origins:
        found = _walk_up_for_dotenv(origin)
        if found is not None:
            return found
    return None


def load_configuration(dot_env_path: Path | None = None) -> None:
    """Populate ``os.environ`` from the project ``.env`` (best-effort).

    If a ``.env`` is found it is loaded via python-dotenv with ``override=False``
    so real environment variables (CI, an exported shell) always win. Where no file
    exists this is a no-op. When *dot_env_path* is None the file is located by walking up
    the tree (see :func:`find_dotenv`), so it resolves regardless of CWD; pass an explicit
    path in tests.
    """
    resolved = find_dotenv() if dot_env_path is None else dot_env_path
    if resolved is not None and resolved.exists():
        # lazy imports keep module import cheap
        from dotenv import dotenv_values, load_dotenv

        # Which names this call is about to *inject* — those in the file and not already in
        # the environment. `override=False` means the rest are real exported variables that
        # win, and after the load the two are indistinguishable in `os.environ`.
        #
        # That mattered exactly once and badly: `FANTABOT_AUTO_ACT` copied in here read as
        # an exported variable for the life of the process, so the server could not be
        # disarmed by editing the very file it came from. `config.live_auto_act` re-reads
        # the file for injected names, and needs this to know which those are.
        injected = {
            name for name in dotenv_values(resolved) if name not in os.environ
        }
        load_dotenv(resolved, override=False)

        # Imported **after** `load_dotenv`, and that is load-bearing rather than tidy:
        # importing `fantabot.config` builds its `settings = Settings()` singleton on the
        # spot, and `Settings` resolves `env_file=".env"` against the working directory. Do
        # it one line earlier and the app binds an empty configuration — no encryption key,
        # no database URL — because the launcher's own `.env` has not reached `os.environ`
        # yet and `app/` has no `.env` of its own. That is the same import-time binding this
        # whole change is about, one layer up.
        from fantabot.config import note_dotenv_injection

        note_dotenv_injection(resolved, injected)
        logger.debug("Loaded configuration from %s (%d injected)", resolved, len(injected))
