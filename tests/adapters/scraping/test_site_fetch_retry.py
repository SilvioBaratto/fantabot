"""`fetch_html` retries a connection that dies while the body is being read."""

from __future__ import annotations

import http.client
from typing import Any

import pytest

from fantabot.adapters.scraping import _site


class _Resp:
    def __init__(self, read: Any) -> None:
        self._read = read

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self._read, BaseException):
            raise self._read
        return bytes(self._read)


@pytest.mark.parametrize(
    "failure",
    [ConnectionResetError("reset"), http.client.IncompleteRead(b"ab", 10)],
)
def test_a_mid_body_failure_is_retried(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    served = [_Resp(failure), _Resp(b"<html>ok</html>")]
    monkeypatch.setattr(_site.urllib.request, "urlopen", lambda *a, **k: served.pop(0))
    monkeypatch.setattr(_site.time, "sleep", lambda s: None)

    assert _site.fetch_html("https://example.invalid/x") == "<html>ok</html>"
    assert served == []
