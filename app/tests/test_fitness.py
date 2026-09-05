"""Architecture fitness tests — the load-bearing invariants (SPEC A6, A7).

Static source scans (no imports of heavy deps, no sockets): they read the tracked source
of the fantabot_app package and assert the two properties the SPEC promises.
"""

from __future__ import annotations

import re
from pathlib import Path

import fantabot_app


def _package_root() -> Path:
    return Path(fantabot_app.__file__).parent


def _source_files(*, under: Path) -> list[Path]:
    return [
        py
        for py in under.rglob("*.py")
        if "tests" not in py.parts and "__pycache__" not in py.parts
    ]


def test_api_holds_no_second_sqlalchemy_engine() -> None:
    """A6 — every DB session comes from fantabot's single ``database_manager``.

    The HTTP adapter (``fantabot_app.api``) must not build its own engine or sessionmaker.
    (The provisioner is deliberately excluded: it uses a *transient* admin engine only to
    CREATE the database before fantabot connects, and disposes it immediately.)
    """
    api_root = _package_root() / "api"
    offenders: list[str] = []
    for py in _source_files(under=api_root):
        text = py.read_text(encoding="utf-8")
        if "create_engine(" in text or re.search(r"\bsessionmaker\(", text):
            offenders.append(str(py.relative_to(_package_root())))
    assert offenders == [], f"second engine in the API adapter: {offenders}"


def test_app_never_handles_a_plaintext_token() -> None:
    """A7 — the app hands sessions/stores to fantabot; fantabot decrypts internally.

    No app module reads a plaintext bearer token or calls decrypt itself, so no token can
    be logged, returned, or repr'd from app code.
    """
    root = _package_root()
    forbidden = ("load_plaintext", ".decrypt(")
    offenders: list[tuple[str, str]] = []
    for py in _source_files(under=root):
        text = py.read_text(encoding="utf-8")
        for term in forbidden:
            if term in text:
                offenders.append((str(py.relative_to(root)), term))
    assert offenders == [], f"app code handling plaintext tokens: {offenders}"


def test_no_bid_or_lineup_submit_wiring_exists() -> None:
    """The app never acts: it does not bid, and it does not submit a lineup.

    Call syntax, not prose: lineup.py's docstring says it *never calls*
    ``teamLineup_submit`` — that mention is fine; an actual ``teamLineup_submit(`` call
    is not.

    **Re-cut to ban the write path rather than a module name.** The list used to hold
    ``"application.asta_room"``, which is the module that also defines ``resolve_room``,
    ``RoomRefused`` and ``RoomFrame`` — none of which can bid. So a read-only room view
    failed this test, while ``rtdb.place_raise`` and ``room.run_bid_loop``, the two
    functions that actually spend credits, were absent from the list and would have
    passed. The guard banned the viewer and permitted the bidder.

    The names below are the acting ones. ``RoomTracker`` is included because its
    ``cycle`` is what decides and places a raise; reading a room's configuration is not
    the same act and is deliberately allowed.
    """
    root = _package_root()
    forbidden = (
        "teamLineup_submit(",  # submits the weekly formazione
        "decide_bid(",  # chooses a raise
        "place_raise(",  # PATCHes it to the RTDB — the one that spends credits
        "run_bid_loop(",  # the loop that calls both, forever
        "RoomTracker(",  # owns the loop and the per-cycle decision
    )
    offenders: list[tuple[str, str]] = []
    for py in _source_files(under=root):
        text = py.read_text(encoding="utf-8")
        for term in forbidden:
            if term in text:
                offenders.append((str(py.relative_to(root)), term))
    assert offenders == [], f"the app wires an action it must not take: {offenders}"


def test_the_boundary_names_the_functions_that_actually_act() -> None:
    """A guard that misses the write path is worse than none — it reassures.

    Pinned as a list rather than a comment because the previous version banned a module
    name and the two functions that spend real credits were not on it.
    """
    source = (Path(__file__).parent / "test_fitness.py").read_text(encoding="utf-8")
    for acting in ("place_raise(", "run_bid_loop(", "decide_bid(", "teamLineup_submit("):
        assert f'"{acting}"' in source, f"{acting} dropped from the v1 boundary"
