"""The RTDB read transport, on `httpx.MockTransport`. **Zero sockets.**

`shard_url` is the routing detail everything downstream depends on — a room's `db` index picks
the Firebase host, and `None` is the *default* namespace, not shard 0. `read_snapshot` is the
one-shot node read the advisory bootstraps from; it must return `None` for an empty node, not an
empty dict, so "no lot" is distinguishable from "a lot with no fields yet".

The boundary tests are the load-bearing half: like `aste/`, nothing on the `fantalab/` capture
path may reach `fantabot.db` — an outage must cost catch-up time, never a bid.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from fantabot.fantalab import rtdb

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "fantabot" / "fantalab"
CAPTURE = ("rest.py", "rtdb.py")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_shard_url_resolves_index_and_default_namespace() -> None:
    assert rtdb.shard_url(9) == "https://fantalab-9.europe-west1.firebasedatabase.app"
    assert rtdb.shard_url(0) == "https://fantalab-0.europe-west1.firebasedatabase.app"
    # None is the DEFAULT namespace, not shard 0
    assert rtdb.shard_url(None) == rtdb.DEFAULT
    assert rtdb.shard_url(None) != rtdb.shard_url(0)


def test_node_url_builds_a_json_path_on_the_shard() -> None:
    url = rtdb.node_url(9, "auction/90c5fa2c-league")
    assert url == (
        "https://fantalab-9.europe-west1.firebasedatabase.app"
        "/auction/90c5fa2c-league.json"
    )
    # leading/trailing slashes on the path are tolerated
    assert rtdb.node_url(9, "/auction/x/") == rtdb.node_url(9, "auction/x")


def test_read_snapshot_returns_the_node_dict() -> None:
    node = {"player_id": "p1", "price": 12, "update_type": "raise"}
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=node)

    got = rtdb.read_snapshot(9, "auction/L", transport=httpx.MockTransport(handler))
    assert got == node
    assert seen["path"] == "/auction/L.json"


def test_read_snapshot_of_an_empty_node_is_none_not_empty_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Firebase returns the literal JSON `null` (not an empty body) for an absent node.
        return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})

    assert rtdb.read_snapshot(9, "auction/L", transport=httpx.MockTransport(handler)) is None


@pytest.mark.parametrize("module", CAPTURE)
def test_fantalab_capture_path_cannot_reach_the_database(module: str) -> None:
    offenders = {name for name in _imports(PACKAGE / module) if name.startswith("fantabot.db")}
    assert offenders == set(), f"{module} can reach the database via {offenders}"


@pytest.mark.parametrize("module", CAPTURE)
def test_fantalab_capture_path_reaches_no_orm(module: str) -> None:
    offenders = {name for name in _imports(PACKAGE / module) if name.startswith("sqlalchemy")}
    assert offenders == set(), f"{module} imports SQLAlchemy: {offenders}"
