"""What is left of ``state.py`` after the port: one path.

The eleven tests that pinned ``load``, ``save``, the defaults-first merge, the
``default=str`` asymmetry and the shallow-copy aliasing did their job — they
described the behaviour precisely enough that replacing it was a decision rather
than an accident — and they retired with the code they covered. Runtime state is
``bot_state`` and ``auction_bids``, both since dropped — they never held a row.

What survives is the one function that must keep working before a database
exists, and the reason it must.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import _importgraph as G
import pytest

from fantabot.adapters.browser import storage_state as state


def test_storage_state_path_comes_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = tmp_path / "storage_state.json"
    monkeypatch.setattr(state.settings, "fantabot_storage_state", expected)

    assert state.storage_state_path() == expected


def test_state_no_longer_carries_runtime_state() -> None:
    """load, save and processed_bids are gone. bot_state and auction_bids
    replace them, keyed by lega — which one flat file could not represent."""
    assert not hasattr(state, "load")
    assert not hasattr(state, "save")
    assert not hasattr(state, "_DEFAULT_STATE")


@pytest.mark.parametrize(
    "module",
    ["fantabot.adapters.browser.storage_state", "fantabot.adapters.browser.capture"],
)
def test_the_browser_chain_does_not_import_the_database(module: str) -> None:
    """SPEC's Never list names **both** files, and only one was ever checked.

    `browser.py` was covered by nothing: this test read `state.py` alone, and
    after `fantabot auth login` replaces `auth`, `fantabot.interface.app` no longer pulls in
    `browser` at all — so no other test catches it incidentally either, while
    `login.py` legitimately imports both `browser` and `fantabot.adapters.persistence`.

    Asked of the import graph, not of the file's lines. Both module docstrings name the
    persistence package in order to explain why they must not import it, and a
    line-by-line scan for that string could not tell the sentence from the import. The
    graph is also transitive, which a scan of one file is not: the layer rules permit an
    adapter to reach persistence, so nothing else makes this claim.
    """
    offenders = [
        target
        for target in ("fantabot.adapters.persistence", "sqlalchemy")
        if G.reaches(module, target)
    ]

    assert offenders == [], f"src/fantabot/{module} reaches the database: {offenders}"


def test_the_browser_chain_can_be_imported_with_no_database() -> None:
    """The guarantee moved, and this is where it moved to.

    It used to be about the `auth` command, which no longer exists. What
    survives it is the *import chain*: `state.py` and `browser.py` must load
    without a database, so `fantabot --help` never becomes a connection attempt
    (SPEC assumption 6).

    `fantabot.application.auth_login` is deliberately **not** imported here. It imports
    `fantabot.adapters.persistence`, legitimately — which is precisely what this test forbids on
    this chain.
    """
    script = textwrap.dedent(
        """
        import socket

        def boom(*args, **kwargs):
            raise AssertionError("a connection was opened at import time")

        socket.socket.connect = boom
        socket.create_connection = boom

        import fantabot.adapters.browser.storage_state
        import fantabot.adapters.browser.capture
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
