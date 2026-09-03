"""F4 — the server host: serve the compiled SPA and open the browser.

Tested on a bare `FastAPI()` with temp dist dirs, so no cross-package import, no real
uvicorn, no real browser. The single-process "API + SPA on one port" boot and the
browser open are exercised for real at the C0 checkpoint.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fantabot_app.server import mount_spa, open_browser, serve


def _built_dist(root: Path) -> Path:
    dist = root / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<h1>APP ROOT</h1>", encoding="utf-8")
    return dist


def test_mount_spa_serves_index_at_root(tmp_path) -> None:
    app = FastAPI()
    mount_spa(app, _built_dist(tmp_path))
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "APP ROOT" in response.text


def test_mount_spa_falls_back_to_index_for_client_routes(tmp_path) -> None:
    app = FastAPI()
    mount_spa(app, _built_dist(tmp_path))
    client = TestClient(app)
    response = client.get("/dashboard")  # an Angular deep link, not a file
    assert response.status_code == 200
    assert "APP ROOT" in response.text


def test_mount_spa_serves_a_real_asset(tmp_path) -> None:
    dist = _built_dist(tmp_path)
    (dist / "main.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()
    mount_spa(app, dist)
    response = TestClient(app).get("/main.js")
    assert response.status_code == 200
    assert "console.log" in response.text


def test_mount_spa_shows_placeholder_when_not_built(tmp_path) -> None:
    app = FastAPI()
    mount_spa(app, tmp_path / "does-not-exist")
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "not built" in response.text.lower()


def test_open_browser_calls_opener_with_url() -> None:
    calls: list[str] = []
    open_browser("http://localhost:8000", opener=calls.append)
    assert calls == ["http://localhost:8000"]


def test_serve_invokes_runner_with_host_and_port() -> None:
    seen: dict[str, object] = {}

    def fake_runner(application, host, port) -> None:
        seen.update(host=host, port=port)

    serve(
        host="127.0.0.1",
        port=8000,
        launch_browser=False,
        app_factory=FastAPI,
        runner=fake_runner,
    )
    assert seen == {"host": "127.0.0.1", "port": 8000}
