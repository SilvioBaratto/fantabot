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
    """SPEC boundary — v1 never wires a live bid or a lineup submit.

    Call/import syntax, not prose: lineup.py's docstring says it *never calls*
    ``teamLineup_submit`` — that mention is fine; an actual ``teamLineup_submit(`` call
    or an ``asta_room`` import is not.
    """
    root = _package_root()
    forbidden = ("teamLineup_submit(", "import asta_room", "application.asta_room", "decide_bid(")
    offenders: list[tuple[str, str]] = []
    for py in _source_files(under=root):
        text = py.read_text(encoding="utf-8")
        for term in forbidden:
            if term in text:
                offenders.append((str(py.relative_to(root)), term))
    assert offenders == [], f"live bid / lineup submit wired in app code: {offenders}"
