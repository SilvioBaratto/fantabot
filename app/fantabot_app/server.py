"""Serve the FastAPI adapter and the compiled Angular SPA from one process.

End users run `fantabot-app`, which starts one uvicorn process that answers the JSON API
(``/api/v1``, ``/health``, ``/docs``) and serves the prebuilt Angular bundle for
everything else, then opens the browser. No Node, no second server.

The compiled frontend is bundled into the package at ``fantabot_app/web`` (added in S13);
until then, ``mount_spa`` serves a small placeholder so the app still boots. The app
factory, runner and browser opener are injected so the wiring is testable without a real
server or browser.
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

_PLACEHOLDER = (
    "<!doctype html><html><head><title>fantabot-app</title></head><body>"
    "<h1>fantabot-app</h1><p>The frontend is not built yet. Build it (S13) or run "
    "<code>npm start</code> in <code>app/frontend</code> for development.</p>"
    "</body></html>"
)


def default_dist() -> Path:
    """The bundled compiled-frontend directory (populated in S13)."""
    return Path(__file__).parent / "web"


def mount_spa(app: FastAPI, dist_dir: Path) -> None:
    """Serve ``dist_dir`` as a single-page app, falling back to index.html.

    Registered last, so the API routes (``/health``, ``/api/v1``, ``/docs``) take
    precedence; the catch-all serves real asset files and returns index.html for unknown
    client-side routes. If the frontend is not built, a placeholder is served instead.
    """
    index = dist_dir / "index.html"

    if not index.exists():

        @app.get("/{_spa_path:path}", include_in_schema=False, response_class=HTMLResponse)
        def _placeholder(_spa_path: str = "") -> str:
            return _PLACEHOLDER

        return

    root = dist_dir.resolve()

    @app.get("/{spa_path:path}", include_in_schema=False)
    def _spa(spa_path: str = "") -> FileResponse:
        candidate = (dist_dir / spa_path).resolve()
        if spa_path and root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def open_browser(url: str, *, opener: Callable[[str], object] = webbrowser.open) -> None:
    """Open ``url`` in the user's default browser."""
    opener(url)


def _default_app_factory() -> FastAPI:
    from app.main import app as fastapi_app

    application: FastAPI = fastapi_app
    return application


def _default_runner(app: FastAPI, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    dist_dir: Path | None = None,
    launch_browser: bool = True,
    opener: Callable[[str], object] = webbrowser.open,
    app_factory: Callable[[], FastAPI] = _default_app_factory,
    runner: Callable[[FastAPI, str, int], None] = _default_runner,
) -> None:
    """Build the app, mount the SPA, optionally open the browser, and run the server."""
    application = app_factory()
    mount_spa(application, dist_dir or default_dist())
    if launch_browser:
        open_browser(f"http://{host}:{port}", opener=opener)
    runner(application, host, port)
