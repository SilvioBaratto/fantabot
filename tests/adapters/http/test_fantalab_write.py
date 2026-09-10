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


def _auto_act(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    """Set the ambient lock where `place_raise` actually looks.

    These tests set `config.settings.fantabot_auto_act`, which `place_raise` read until the
    singleton turned out to be bound at first import — so a bid loop running all evening
    kept the lock it booted with, and `.env` could not disarm it. `config.live_auto_act`
    re-reads per call; an environment variable with nothing recorded as dotenv-injected is
    its "genuinely exported" branch.
    """
    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true" if on else "false")


def test_auto_act_off_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _auto_act(monkeypatch, False)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=PAYLOAD)

    out = rtdb.place_raise(9, "L", PAYLOAD, transport=httpx.MockTransport(handler))

    assert calls["n"] == 0  # no PATCH was issued
    assert out.dry_run is True and out.sent is False and out.status is None
    assert out.price == 6


def test_auto_act_on_sends_the_documented_payload_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _auto_act(monkeypatch, True)
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
    _auto_act(monkeypatch, True)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    rtdb.place_raise(9, "L", PAYLOAD, node="assign", transport=httpx.MockTransport(handler))
    assert seen["path"] == "/assign/L.json"


def test_a_token_never_surfaces_in_the_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _auto_act(monkeypatch, True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    out = rtdb.place_raise(
        9, "L", PAYLOAD, token="a-secret-token-value", transport=httpx.MockTransport(handler)
    )
    assert "a-secret-token-value" not in repr(out)


def test_the_lock_is_re_read_between_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The evening property: disarming mid-run must stop the very next bid.

    `CLAUDE.md`: *"the operator who edits it in the morning is not the one at the keyboard at
    21:47."* `asta bid` is a single process that polls for hours, so "read once per process"
    and "read once per bid" are different things there — and the module docstring claimed the
    second while doing the first. A value bound at import kept bidding after the operator
    disarmed, which is the one failure this lock exists to prevent.
    """
    sent = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["n"] += 1
        return httpx.Response(200, json=PAYLOAD)

    transport = httpx.MockTransport(handler)

    _auto_act(monkeypatch, True)
    assert rtdb.place_raise(9, "L", PAYLOAD, transport=transport).sent is True
    assert sent["n"] == 1

    _auto_act(monkeypatch, False)  # the operator disarms, same process
    out = rtdb.place_raise(9, "L", PAYLOAD, transport=transport)

    assert out.sent is False and out.dry_run is True
    assert sent["n"] == 1, "a bid was sent after the operator disarmed"
