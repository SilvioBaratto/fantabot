"""Asking FantaLab which auctions are live. One authenticated GET.

Spike S2 established this is a plain REST endpoint rather than the Firebase
subscription the field notes first claimed — a conclusion drawn from watching a
filter toggle, which fires nothing, instead of the initial page load.

```
GET api.fantalab.it/fantaleagues/live      401 unauthenticated · 200 with a session
Authorization: Bearer <id_token>           measured 2026-08-27: 189 auctions
```

**``asta_type`` is deliberately omitted.** It is an optional filter, and passing
it is how the poller threw away 85% of the population. We play both formats, so
the format is a column to select on later, never a decision taken here.

The transport is injected, so the parsing and the failure handling are testable
without a socket — the same seam ``stream.py`` uses, for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from fantabot.aste.registry import AuctionConfig, from_card

LIVE_URL = "https://api.fantalab.it/fantaleagues/live"


class AuthExpired(RuntimeError):
    """The stored session no longer authenticates.

    Its own type because the remedy is specific and human: the id_token lasts
    about an hour from capture, and refreshing it is not yet implemented (see
    ``docs/fantalab/05`` §3c). Returning an empty list instead would be
    indistinguishable from a night with no auctions.
    """


class ScanEmpty(RuntimeError):
    """The endpoint answered, and said nothing is live.

    Also its own type, and also not silently swallowed. Zero auctions is real at
    five in the morning and identical, from the caller's side, to a scan that
    has quietly broken. The caller is told which and decides.
    """


class Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


Getter = Callable[[str, dict[str, str]], Response]


def _httpx_get(url: str, headers: dict[str, str]) -> Response:
    import httpx

    return httpx.get(url, headers=headers, timeout=30)


class LiveAuctionsClient:
    """Reads the live-auction list with a stored session."""

    def __init__(self, token: str, get: Getter | None = None) -> None:
        self._token = token
        self._get = get or _httpx_get

    def live_auctions(self) -> Sequence[AuctionConfig]:
        """Every live auction, in every format.

        Raises rather than returning empty on both failure modes, because both
        of them look like success from a caller that only counts rows.
        """
        # The credential goes in a header and never in the URL: a URL reaches
        # logs, proxies and error messages that a header does not.
        response = self._get(LIVE_URL, {"Authorization": f"Bearer {self._token}"})

        if response.status_code == 401:
            raise AuthExpired(
                "the stored FantaLab session no longer authenticates — "
                "run `fantabot fantalab-login --force`"
            )
        if response.status_code != 200:
            raise RuntimeError(f"{LIVE_URL} answered {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"{LIVE_URL} did not answer with a list")
        if not payload:
            raise ScanEmpty("the endpoint reports no live auctions")

        # from_card validates the shard, which lands in a hostname and arrives
        # here from a remote response.
        return [from_card(card) for card in payload]
