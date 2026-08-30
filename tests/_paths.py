"""Every path the suite needs, resolved once.

Thirty test files computed their own paths from `Path(__file__)`, and about half of them
walked into a *named source subdirectory* — `src/fantabot/asta_engine`,
`src/fantabot/aste`, `src/fantabot/mantra_grid`. Those are the ones W6 breaks: the move
renames the directories, and each of those literals then points at nothing.

A path that points at nothing does not always fail loudly. `Path.rglob` on a missing
directory yields nothing, so a text or AST scan over a moved package passes with zero
files examined — the exact shape of `test_the_shipped_grid_and_matrix_pass_their_own
_gates`, which judged fixtures for a week while the artefact it was supposed to judge held
one entry. Several of the scans here are structured that way.

`pkg()` is the point of the module. It maps a *logical* package name to wherever that
package currently lives, so the move is one edit in the table below rather than fourteen
across the suite, and it raises on a name it does not know rather than handing back a
directory that is not there.
"""

from __future__ import annotations

from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
SRC = REPO / "src"
PACKAGE = SRC / "fantabot"

FIXTURES = TESTS / "fixtures"
GOLDEN = TESTS / "golden"
INTEGRATION = TESTS / "integration"

#: The recorded evening every reconstruction test replays.
ONE_AUCTION = FIXTURES / "states" / "one_auction.jsonl"
SSE_FIXTURES = FIXTURES / "sse"

#: Logical package name -> its directory today. W6 edits the values here and nothing else.
#: Keep every name a test asks for, including ones whose location has not changed: the
#: value of the indirection is that no test spells a directory itself.
_PACKAGES: dict[str, Path] = {
    "agentkit": PACKAGE / "agentkit",
    "asta_engine": PACKAGE / "asta_engine",
    "aste": PACKAGE / "aste",
    "data_sources": PACKAGE / "data_sources",
    "db": PACKAGE / "adapters" / "persistence",
    "fantalab": PACKAGE / "fantalab",
    "mantra_grid": PACKAGE / "mantra_grid",
    "news": PACKAGE / "news",
    "scrapers": PACKAGE / "scrapers",
    "tokens": PACKAGE / "tokens",
}


def pkg(name: str) -> Path:
    """The directory of one source package.

    Raises on an unknown name, and on a known name whose directory has gone. Both are
    the same failure -- a scan over a directory that is not there examines no files and
    reports success -- and a test suite that says "0 problems found" because it looked
    nowhere is worse than one that fails.
    """
    try:
        path = _PACKAGES[name]
    except KeyError:
        raise KeyError(
            f"no source package named {name!r}; known: {sorted(_PACKAGES)}. "
            "If it was renamed, update the table in tests/_paths.py."
        ) from None
    if not path.is_dir():
        raise FileNotFoundError(
            f"tests/_paths.py maps {name!r} to {path}, which does not exist. "
            "A scan over a missing directory finds nothing and passes; fix the table."
        )
    return path
