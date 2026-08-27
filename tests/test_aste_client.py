"""The live-auctions client: an authenticated GET, parsed into configs.

No socket opens — the transport is injected, as it is for the stream. What is
pinned is the handling of the two answers that are easy to get wrong: an empty
list, which looks exactly like a quiet night, and a 401, which looks like
nothing at all if it is swallowed.
"""

from __future__ import annotations

import pytest

from fantabot.aste.client import LIVE_URL, AuthExpired, LiveAuctionsClient, ScanEmpty


def _card(auction_id: str, asta_type: str = "mantra") -> dict[str, object]:
    return {
        "fantaleague_id": auction_id,
        "db": 15,
        "asta_type": asta_type,
        "fantaleague_name": "Lega",
        "num_teams": 8,
        "num_credits": 500,
        "asta_mode": "random",
        "raise_mode": "free",
    }


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _client(response: _Response, recorder: list | None = None) -> LiveAuctionsClient:
    def get(url: str, headers: dict[str, str]) -> _Response:
        if recorder is not None:
            recorder.append((url, headers))
        return response

    return LiveAuctionsClient(token="tok", get=get)


def test_every_format_is_returned_because_the_filter_is_omitted() -> None:
    """`asta_type` is an optional query parameter. Omitting it is how the scan
    stops throwing away 85% of the population — we play both formats."""
    recorder: list = []
    configs = _client(
        _Response(200, [_card("a", "mantra"), _card("b", "classic")]), recorder
    ).live_auctions()
    url, _ = recorder[0]
    assert "asta_type" not in url
    assert url == LIVE_URL
    assert {c.asta_type for c in configs} == {"mantra", "classic"}


def test_the_token_travels_in_the_header_and_nowhere_else() -> None:
    recorder: list = []
    _client(_Response(200, [_card("a")]), recorder).live_auctions()
    url, headers = recorder[0]
    assert headers["Authorization"] == "Bearer tok"
    assert "tok" not in url, "a credential must never reach a URL"


def test_an_expired_session_is_named_not_swallowed() -> None:
    """A 401 means the id_token aged out — about an hour after capture. Silently
    returning nothing would look exactly like a night with no auctions."""
    with pytest.raises(AuthExpired, match="fantalab-login"):
        _client(_Response(401, {"error": "unauthorized"})).live_auctions()


def test_an_empty_list_is_refused_rather_than_returned() -> None:
    """Zero live auctions is possible at 5am and indistinguishable from a broken
    scan. The caller is told, and decides."""
    with pytest.raises(ScanEmpty):
        _client(_Response(200, [])).live_auctions()


def test_a_hostile_shard_is_refused_at_the_boundary() -> None:
    """The response is remote content. `db` lands in a hostname."""
    from fantabot.aste.models import ShardError

    bad = _card("a")
    bad["db"] = "evil.com#"
    with pytest.raises(ShardError):
        _client(_Response(200, [bad])).live_auctions()


def test_an_unexpected_status_is_reported_with_its_code() -> None:
    with pytest.raises(RuntimeError, match="503"):
        _client(_Response(503, {})).live_auctions()
