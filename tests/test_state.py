"""What is left of ``state.py`` after the port: one path.

The eleven tests that pinned ``load``, ``save``, the defaults-first merge, the
``default=str`` asymmetry and the shallow-copy aliasing did their job — they
described the behaviour precisely enough that replacing it was a decision rather
than an accident — and they retired with the code they covered. Runtime state is
``bot_state`` and ``auction_bids`` now, tested in tests/integration/.

What survives is the one function that must keep working before a database
exists, and the reason it must.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fantabot import state


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


@pytest.mark.parametrize("module", ["state.py", "browser.py"])
def test_the_browser_chain_does_not_import_the_database(module: str) -> None:
    """SPEC's Never list names **both** files, and only one was ever checked.

    `browser.py` was covered by nothing: this test read `state.py` alone, and
    after `fantabot login` replaces `auth`, `fantabot.cli` no longer pulls in
    `browser` at all — so no other test catches it incidentally either, while
    `login.py` legitimately imports both `browser` and `fantabot.db`.

    Import statements, not prose: both module docstrings name `fantabot.db` in
    order to explain why they must not import it.
    """
    imports = [
        line
        for line in Path(f"src/fantabot/{module}").read_text().splitlines()
        if line.startswith(("import ", "from "))
    ]
    offenders = [line for line in imports if "fantabot.db" in line or "sqlalchemy" in line]

    assert offenders == [], f"src/fantabot/{module} reaches the database: {offenders}"


def test_the_auth_path_can_be_imported_with_no_database() -> None:
    script = textwrap.dedent(
        """
        import socket

        def boom(*args, **kwargs):
            raise AssertionError("a connection was opened at import time")

        socket.socket.connect = boom
        socket.create_connection = boom

        import fantabot.state
        import fantabot.browser
        import fantabot.auth
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
