"""Build the Angular frontend and stage it into ``fantabot_app/web`` for the wheel.

Run this before ``uv tool install ./app`` (or in CI) so the wheel ships the compiled SPA
and end users need no Node. Requires Node + npm on PATH. The built bundle is a build
artifact (git-ignored); hatchling includes ``fantabot_app/web`` in the wheel when present,
and the server falls back to a placeholder when it is not.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
FRONTEND = APP / "frontend"
BUILT = FRONTEND / "dist" / "frontend" / "browser"
WEB = APP / "fantabot_app" / "web"
_SHELL = sys.platform == "win32"  # npm/npx are .cmd shims on Windows


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=FRONTEND, check=True, shell=_SHELL)


def main() -> None:
    _run(["npm", "ci"])
    _run(["npx", "ng", "build"])
    if not (BUILT / "index.html").exists():
        raise SystemExit(f"frontend build produced no index.html at {BUILT}")
    if WEB.exists():
        shutil.rmtree(WEB)
    shutil.copytree(BUILT, WEB)
    count = sum(1 for path in WEB.rglob("*") if path.is_file())
    print(f"staged {count} files into {WEB}")


if __name__ == "__main__":
    main()
