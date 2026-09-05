"""One confirmed read of the credential: when it returns, and when it says "not yet".

Every case injects `sleep`, so this file sleeps zero seconds and opens zero sockets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from fantabot.application.login_wait import (
    CaptureUnreadable,
    CredentialNotThere,
    read_credential,
)
from fantabot.domain.tokens.errors import (
    SignInWindowClosed,
    StorageReadFailed,
    TokenError,
)
from fantabot.interface.console import console


@dataclass
class _Naps:
    """Counts sleeps instead of taking them."""

    count: int = 0

    def sleep(self, _seconds: float) -> None:
        self.count += 1


@dataclass(frozen=True)
class _Session:
    complete: bool


def _complete(session: _Session) -> bool:
    return session.complete


def _state() -> Mapping[str, Any]:
    return {}


def test_a_written_credential_is_returned_on_the_first_read() -> None:
    naps = _Naps()
    reads = {"n": 0}

    def parse(_s: Mapping[str, Any]) -> _Session:
        reads["n"] += 1
        return _Session(complete=True)

    result = read_credential(
        _state,
        parse,
        is_complete=_complete,
        not_ready=(TokenError,),
        report=console,
        sleep=naps.sleep,
    )

    assert result.complete is True
    assert reads["n"] == 1
    assert naps.count == 0


def test_confirming_too_early_costs_exactly_one_read() -> None:
    """The whole point of the change.

    Each read makes Playwright walk every visited origin, opening a temporary tab for
    any without a page — a burst across the login form. The old loop did that thirty
    times over a minute and then killed the job. Now an early confirmation reads once
    and hands control back so the caller can ask again.
    """
    naps = _Naps()
    reads = {"n": 0}

    def parse(_s: Mapping[str, Any]) -> _Session:
        reads["n"] += 1
        raise TokenError("no leghe in the browser yet")

    with pytest.raises(CredentialNotThere):
        read_credential(
            _state,
            parse,
            is_complete=_complete,
            not_ready=(TokenError,),
            report=console,
            sleep=naps.sleep,
        )

    assert reads["n"] == 1
    assert naps.count == 0  # and no waiting either


def test_an_error_outside_not_ready_is_terminal() -> None:
    """A malformed credential is not an early one.

    `parse_storage_state` raises `NoLeaguesFound` while the blob is absent but
    `LeagueMismatch` when it is present and decodes to a different lega. Confirming
    again cannot fix the second, so it must not be reported as "not signed in yet".
    """

    class Malformed(TokenError):
        pass

    class NotReady(TokenError):
        pass

    def parse(_s: Mapping[str, Any]) -> _Session:
        raise Malformed("this blob decodes to a different lega")

    with pytest.raises(Malformed):
        read_credential(
            _state,
            parse,
            is_complete=_complete,
            not_ready=(NotReady,),
            report=console,
            sleep=_Naps().sleep,
        )


def test_an_incomplete_credential_is_held_until_it_stops_changing() -> None:
    """The half-write guard, and the one path where extra reads earn their cost.

    `parse_fantalab_storage` requires `refresh_token` and `user_id` but treats
    `id_token` and `access_token` as optional, so a read landing mid-write parses
    cleanly and yields a thinner session than the browser is about to finish writing.
    """
    naps = _Naps()
    reads = {"n": 0}

    def parse(_s: Mapping[str, Any]) -> _Session:
        reads["n"] += 1
        return _Session(complete=False)

    result = read_credential(
        _state,
        parse,
        is_complete=_complete,
        not_ready=(TokenError,),
        report=console,
        sleep=naps.sleep,
    )

    assert result.complete is False
    assert reads["n"] == 5  # SETTLE_TICKS, not the first successful parse


def test_a_credential_that_finishes_arriving_wins_immediately() -> None:
    """Settling must not delay one that completes."""
    naps = _Naps()
    reads = {"n": 0}

    def parse(_s: Mapping[str, Any]) -> _Session:
        reads["n"] += 1
        return _Session(complete=reads["n"] >= 3)

    result = read_credential(
        _state,
        parse,
        is_complete=_complete,
        not_ready=(TokenError,),
        report=console,
        sleep=naps.sleep,
    )

    assert result.complete is True
    assert reads["n"] == 3


def test_a_closed_window_aborts_rather_than_asking_again() -> None:
    naps = _Naps()

    def read() -> Mapping[str, Any]:
        raise SignInWindowClosed()

    with pytest.raises(SignInWindowClosed):
        read_credential(
            read,
            lambda _s: _Session(complete=True),
            is_complete=_complete,
            not_ready=(TokenError,),
            report=console,
            sleep=naps.sleep,
        )


def test_a_flaky_read_is_retried_rather_than_reported_as_missing() -> None:
    """One third-party origin failing to load is the collector, not the credential.

    Observed live as `net::ERR_ABORTED` while navigating to
    safeframe.googlesyndication.com. Reporting it as "not signed in yet" would send the
    human back to a browser they had already finished with.
    """
    naps = _Naps()
    reads = {"n": 0}

    def read() -> Mapping[str, Any]:
        reads["n"] += 1
        if reads["n"] in (1, 2):
            raise StorageReadFailed()
        return {}

    result = read_credential(
        read,
        lambda _s: _Session(complete=True),
        is_complete=_complete,
        not_ready=(TokenError,),
        report=console,
        sleep=naps.sleep,
    )

    assert result.complete is True
    assert reads["n"] == 3


def test_a_run_of_failed_reads_gives_up_and_says_so() -> None:
    naps = _Naps()

    def read() -> Mapping[str, Any]:
        raise StorageReadFailed()

    with pytest.raises(CaptureUnreadable) as excinfo:
        read_credential(
            read,
            lambda _s: _Session(complete=True),
            is_complete=_complete,
            not_ready=(TokenError,),
            report=console,
            sleep=naps.sleep,
        )

    assert "Nothing was written" in str(excinfo.value)


def test_nothing_it_prints_is_derived_from_what_it_read() -> None:
    """This holds a credential on every confirmation."""
    secret = "eyJhbGciOiJIUzI1NiJ9.super-secret-value"

    def read() -> Mapping[str, Any]:
        return {"origins": [{"localStorage": [{"name": "t", "value": secret}]}]}

    def parse(_s: Mapping[str, Any]) -> _Session:
        return _Session(complete=False)

    with console.capture() as captured:
        read_credential(
            read,
            parse,
            is_complete=_complete,
            not_ready=(TokenError,),
            report=console,
            sleep=_Naps().sleep,
        )

    printed = captured.get()
    assert secret not in printed
    assert "super-secret" not in printed
    assert str(len(secret)) not in printed
