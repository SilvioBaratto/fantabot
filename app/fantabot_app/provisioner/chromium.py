"""Install the Playwright chromium the headed connect-account login uses.

Runs ``python -m playwright install chromium`` once at ``setup``. The runner is injected
so the step is unit-testable without spawning a download.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence


def _default_run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


def install_chromium(*, run: Callable[[Sequence[str]], None] = _default_run) -> None:
    run([sys.executable, "-m", "playwright", "install", "chromium"])
