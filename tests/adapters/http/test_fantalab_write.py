"""The gated bid write, on `httpx.MockTransport`. **Zero sockets, zero real bids.**

Two guarantees. With `FANTABOT_AUTO_ACT` off — the default — `place_raise` must send nothing at
all: the mock handler never being called is how that is proved (an assertion on the return value
alone would pass even if a socket had been opened first). With it on, the exact documented
payload is PATCHed once. And a token, if ever supplied, must not surface in the outcome.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from fantabot import config
from fantabot.adapters.http.fantalab import rtdb

PAYLOAD: dict[str, Any] = {
    "price": 6,
    "fantaleague_id": "L",
    "user_id": "me",
    "fantateam_id": "seat2",
    "player_id": "kean",
    "is_first": False,
    "update_type": "raise",
    "last_bid_time": {".sv": "timestamp"},
    "last_update": 1_000_000,
}


def test_auto_act_off_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "fantabot_auto_act", False)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=PAYLOAD)

    out = rtdb.place_raise(9, "L", PAYLOAD, transport=httpx.MockTransport(handler))

    assert calls["n"] == 0  # no PATCH was issued
    assert out.dry_run is True and out.sent is False and out.status is None
    assert out.price == 6


def test_auto_act_on_sends_the_documented_payload_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "fantabot_auto_act", True)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=PAYLOAD)

    out = rtdb.place_raise(9, "L", PAYLOAD, transport=httpx.MockTransport(handler))

    assert seen["method"] == "PATCH"
    assert seen["path"] == "/auction/L.json"
    assert seen["body"] == PAYLOAD
    assert out.sent is True and out.status == 200 and out.dry_run is False


def test_assign_node_is_addressable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "fantabot_auto_act", True)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    rtdb.place_raise(9, "L", PAYLOAD, node="assign", transport=httpx.MockTransport(handler))
    assert seen["path"] == "/assign/L.json"


def test_a_token_never_surfaces_in_the_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "fantabot_auto_act", True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    out = rtdb.place_raise(
        9, "L", PAYLOAD, token="a-secret-token-value", transport=httpx.MockTransport(handler)
    )
    assert "a-secret-token-value" not in repr(out)
