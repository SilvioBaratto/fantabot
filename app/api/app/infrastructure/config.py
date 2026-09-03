"""Environment-adaptive configuration loader.

Locates the project ``.env`` by walking up the directory tree and loads it into
``os.environ`` before any Settings instance is created. This is what lets the
app find the ROOT-level ``.env`` (one directory above ``api/``) regardless of the
current working directory — ``cd api && uvicorn`` and running from the project
root both resolve the same file. In Docker no ``.env`` exists (compose injects the
vars via ``env_file``), so the load step is simply skipped.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Markers of the PROJECT ROOT (where the .env and docker-compose.yml live). The
# walk stops here so the search never escapes into a parent repo or the
# filesystem root.
_ROOT_MARKERS = ("docker-compose.yml", ".git")


def _walk_up_for_dotenv(origin: Path) -> Path | None:
    """Ascend from *origin*, returning the first ``.env`` found.

    Stops at a project-root marker (docker-compose.yml / .git) so the search
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
    so real environment variables (Docker, CI, exported shell) always win. In
    Docker no file exists and this is a no-op. When *dot_env_path* is None the
    file is located by walking up the tree (see :func:`find_dotenv`), so it
    resolves regardless of CWD; pass an explicit path in tests.
    """
    resolved = find_dotenv() if dot_env_path is None else dot_env_path
    if resolved is not None and resolved.exists():
        from dotenv import load_dotenv  # lazy import keeps module import cheap

        load_dotenv(resolved, override=False)
        logger.debug("Loaded configuration from %s", resolved)
