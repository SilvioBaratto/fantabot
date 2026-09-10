"""`asta optimize` and `asta bid` plan on the lega's band, and detect its format (2.1, 2.2).

Both built a bare `RosterRules()` — **size 30, whatever the lega declares** — while
`GET /asta/plan` read the snapshot. On 2026-08-26 the lega declared 30/30; on 2026-09-02 it
declared **25/32 with `minrl=[2, 23]`**. A plan built on 30 against a 25-man lega is not
slightly wrong, it is unbuyable, and `1.14` found it the moment the parity tier ran the real
command: *"cannot complete the roster: 19/30 filled"*.

The format goes with the band, because `role_groups` decides both and neither caller can use
one without the other — planning a Classic lega against the eleven Mantra schemi is a failure
the band alone would not prevent. `--format` survives as an override and says so when it
disagrees, which is the lineup path's settled answer (`sroles`, detected per run) applied
here: a per-lega flag the operator must remember is a footgun on a cron path.
"""

from __future__ import annotations

from typing import Any

import pytest

from fantabot.domain.asta.state import ASSUMED_NOTHING, SNAPSHOT_DECLARED, RosterRules
from fantabot.domain.classic.state import ClassicRosterRules
from fantabot.interface.asta import _lega_rules


class _Reader:
    """Stands in for `application.lega_reads.rules_for_league`. Records what it was asked."""

    def __init__(self, answer: tuple[Any, str, str]) -> None:
        self.answer = answer
        self.asked: list[int] = []

    def __call__(self, _session: object, league_id: int) -> tuple[Any, str, str]:
        self.asked.append(league_id)
        return self.answer


def _patch(monkeypatch: pytest.MonkeyPatch, reader: _Reader, *, configured: int = 0) -> list[str]:
    from fantabot.application import lega_reads
    from fantabot.config import settings

    monkeypatch.setattr(lega_reads, "rules_for_league", reader)
    monkeypatch.setattr(settings, "fantabot_league_id", configured, raising=False)
    return []


def test_no_lega_plans_on_the_built_in_band_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-2.1 behaviour, kept and labelled. `--lega 0` with nothing configured means
    "plan on the default", which is what the goldens pin."""
    reader = _Reader((RosterRules(size=99), SNAPSHOT_DECLARED, "mantra"))
    _patch(monkeypatch, reader)

    rules, provenance, fmt = _lega_rules(0, "", session=_boom, warn=_ignore)

    assert rules == RosterRules()
    assert provenance == ASSUMED_NOTHING
    assert fmt == "mantra"
    assert reader.asked == [], "it read a lega it was not given"


def test_no_lega_opens_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """`session` is a factory for this reason: the golden harness serves a sentinel in place
    of a session, and a command that opened one unconditionally would either blow up there
    or — worse, on a real machine — make the pinned output depend on the database."""
    _patch(monkeypatch, _Reader((RosterRules(), SNAPSHOT_DECLARED, "mantra")))

    _lega_rules(0, "", session=_boom, warn=_ignore)  # `_boom` raises if called


def test_an_explicit_lega_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _Reader((RosterRules(size=25, min_goalkeepers=2, min_movement=23),
                      SNAPSHOT_DECLARED, "mantra"))
    _patch(monkeypatch, reader)

    rules, provenance, _fmt = _lega_rules(4103937, "", session=_fake_session, warn=_ignore)

    assert reader.asked == [4103937]
    assert rules.size == 25, "the 2026-09-02 band, not the hardcoded 30"
    assert provenance == SNAPSHOT_DECLARED


def test_the_configured_lega_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`FANTABOT_LEAGUE_ID`, the way every other command resolves a lega."""
    reader = _Reader((RosterRules(size=25), SNAPSHOT_DECLARED, "mantra"))
    _patch(monkeypatch, reader, configured=3584692)

    _lega_rules(0, "", session=_fake_session, warn=_ignore)

    assert reader.asked == [3584692]


def test_the_format_is_detected_rather_than_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2.2. Nothing passed, and a Classic lega plans Classic."""
    reader = _Reader((ClassicRosterRules(size=25), SNAPSHOT_DECLARED, "classic"))
    _patch(monkeypatch, reader)

    rules, provenance, fmt = _lega_rules(3584692, "", session=_fake_session, warn=_ignore)

    assert fmt == "classic"
    assert isinstance(rules, ClassicRosterRules)
    assert provenance == SNAPSHOT_DECLARED


def test_an_override_that_agrees_with_the_lega_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _Reader((ClassicRosterRules(size=25), SNAPSHOT_DECLARED, "classic"))
    _patch(monkeypatch, reader)
    said: list[str] = []

    _rules, provenance, fmt = _lega_rules(
        3584692, "classic", session=_fake_session, warn=said.append
    )

    assert (fmt, provenance) == ("classic", SNAPSHOT_DECLARED)
    assert said == []


def test_an_override_that_contradicts_the_lega_says_so_and_drops_the_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning a lega as something it is not is a thing to do deliberately or not at all —
    so it is announced, and the lega's band goes with the lega's format rather than being
    grafted onto the other one."""
    reader = _Reader((ClassicRosterRules(size=25), SNAPSHOT_DECLARED, "classic"))
    _patch(monkeypatch, reader)
    said: list[str] = []

    rules, provenance, fmt = _lega_rules(
        3584692, "mantra", session=_fake_session, warn=said.append
    )

    assert fmt == "mantra"
    assert isinstance(rules, RosterRules)
    assert provenance == ASSUMED_NOTHING, "a grafted band is not the lega's, and must not claim to be"
    assert said and "classic" in said[0] and "--format" in said[0]


class TestTheCommandsExposeIt:
    @pytest.mark.parametrize("command", ["asta_optimize", "asta_bid"])
    def test_the_lega_is_an_option(self, command: str) -> None:
        import inspect

        from fantabot.interface import asta

        assert "lega" in inspect.signature(getattr(asta, command)).parameters

    def test_format_defaults_to_detection_not_to_mantra(self) -> None:
        """Before 2.2 `--format` defaulted to `"mantra"`, so a Classic lega planned Mantra
        unless the operator remembered the flag — on a cron path, nobody remembers."""
        import inspect

        from fantabot.interface import asta

        default = inspect.signature(asta.asta_optimize).parameters["fmt"].default

        assert getattr(default, "default", default) == ""


def _fake_session() -> object:
    return object()


def _boom() -> object:
    raise AssertionError("a database session was opened with no lega to read")


def _ignore(_note: str) -> None:
    return None
