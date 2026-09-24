"""Asking FantaLab which auctions are live. One authenticated GET.

Spike S2 established this is a plain REST endpoint rather than the Firebase
subscription the field notes first claimed — a conclusion drawn from watching a
filter toggle, which fires nothing, instead of the initial page load.

```
GET api.fantalab.it/fantaleagues/live      401 unauthenticated · 200 with a session
Authorization: Bearer <id_token>           measured 2026-08-27: 189 auctions
```

The host in that line is the **default**, not a constant: :func:`_live_url` resolves
``FANTABOT_FANTALAB_BASE_URL`` from the environment on every call and falls back to the
value `Settings` declares. This is the only wrapper of the endpoint — ``fantalab/rest.py``
carried a second, config-respecting one (``live_leagues``) that nothing called, and the two
were collapsed into this one on 2026-09-24 rather than left to disagree.

**``asta_type`` is deliberately omitted.** It is an optional filter, and passing
it is how the poller threw away 85% of the population. We play both formats, so
the format is a column to select on later, never a decision taken here.

The transport is injected, so the parsing and the failure handling are testable
without a socket — the same seam ``stream.py`` uses, for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from fantabot.domain.harvest.registry import AuctionConfig, from_card

LIVE_PATH = "/fantaleagues/live"
"""The endpoint's path. The host is configured, so the whole URL cannot be a constant."""

BASE_URL_FIELD = "fantabot_fantalab_base_url"
"""The `Settings` field the host comes from, spelled once.

Both the variable :func:`_live_url` reads and the value it falls back to are derived from
it: `Settings.model_config` declares no ``env_prefix``, so pydantic-settings resolves the
field from the field name uppercased, and the declared default is the field's own. Writing
either out a second time is how a renamed setting goes on reading a name nothing sets, or
falls back to a host `config.py` stopped declaring — silently, in both directions.
"""

BASE_URL_VAR = BASE_URL_FIELD.upper()
"""``FANTABOT_FANTALAB_BASE_URL``, as the operator exports it or types it into `.env`.

Derived rather than transcribed, and checked against pydantic's own resolution in
`tests/adapters/http/test_aste_client.py` — a name only this module believes in would read
a variable nobody sets while `Settings` read another.
"""


def _live_url() -> str:
    """The live-list URL, with the host read from the environment **on every call**.

    It used to be the whole URL, hardcoded. ``FANTABOT_FANTALAB_BASE_URL`` therefore
    moved ``rest.fetch_league`` and ``rest.join_team`` and left the scan pointed at the
    real site — the worst half-obedience available to a knob whose only purpose is to
    point the app somewhere else, because nothing said the scan had not moved.

    **Read through `config.live_setting`, not off the `settings` singleton**, which is the
    second half of that same defect rather than a flourish. `settings` is built once, at
    first import of `fantabot.config`, so a field read off it answers with the state of the
    world at that moment: a variable exported after the process started, or a `.env` edited
    under a running `fantabot_app`, is obeyed by the next CLI invocation and ignored by the
    server — so the scan behind ``POST /actions/harvest-scan`` would go on hitting the real
    site while a `fantabot config-check` typed in a terminal, being a fresh process, printed
    the override. That is the failure `config.live_auto_act` was written for, and this reads
    the same way: `os.environ` first, because an exported variable
    is the operator speaking later than the file, and otherwise the `.env` itself, re-read
    from disk, for the names a launcher copied out of it at boot.

    `live_setting` returns the raw string and leaves the parsing here, which is `config.py`'s
    rule and here means deciding what *unset* is: absent, empty, whitespace or a bare ``/``
    all mean unset, and unset is the default `Settings` declares. Never ``None`` and never an
    empty host — that would leave the relative path ``/fantaleagues/live``, which is not a
    misconfiguration any reader of the resulting error would recognise as one.

    ⚠ A site that reads ``settings.fantabot_fantalab_base_url`` directly is bound to process
    start — ``rest._base_url`` does, as of 2026-09-24. Both obey the variable; on a change
    made *mid-process* they differ in when.
    """
    from fantabot.config import Settings, live_setting

    configured = (live_setting(BASE_URL_VAR) or "").strip().rstrip("/")
    base = configured or str(Settings.model_fields[BASE_URL_FIELD].default).rstrip("/")
    return f"{base}{LIVE_PATH}"


class AuthExpired(RuntimeError):
    """The stored session no longer authenticates.

    Its own type because the remedy is specific and human.

    Not because expiry is expected: the tokens are Keycloak's and the stored
    ``id_token`` is valid into 2032 (``docs/fantalab/05`` §3c). This fires when a
    session is revoked, rotated elsewhere, or was never stored — and returning an
    empty list instead would be indistinguishable from a night with no auctions.
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

    @classmethod
    def from_store(
        cls, store: Any, user_id: str | None = None, get: Getter | None = None
    ) -> LiveAuctionsClient:
        """Build one from the encrypted store, resolving the bearer here.

        The seam `apileague.auth_headers(league_id, store=...)` already has. A caller
        that reads `store.load().id_token` to pass it in is holding a credential for the
        length of that expression, and every new consumer repeats the mistake. Callers
        hand over the store; only this module ever names a token.

        ``store`` is typed loosely so this module does not import the token package for
        a type it only forwards.
        """
        from fantabot.adapters.http.fantalab.rest import bearer_from

        return cls(bearer_from(store, user_id), get=get)

    def live_auctions(self) -> Sequence[AuctionConfig]:
        """Every live auction, in every format.

        Raises rather than returning empty on both failure modes, because both
        of them look like success from a caller that only counts rows.
        """
        url = _live_url()
        # The credential goes in a header and never in the URL: a URL reaches
        # logs, proxies and error messages that a header does not.
        response = self._get(url, {"Authorization": f"Bearer {self._token}"})

        if response.status_code == 401:
            raise AuthExpired(
                "the stored FantaLab session no longer authenticates — "
                "run `fantabot auth fantalab-login --force`"
            )
        if response.status_code != 200:
            raise RuntimeError(f"{url} answered {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"{url} did not answer with a list")
        if not payload:
            raise ScanEmpty("the endpoint reports no live auctions")

        # from_card validates the shard, which lands in a hostname and arrives
        # here from a remote response.
        return [from_card(card) for card in payload]
