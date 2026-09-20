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

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
import typer

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


def test_the_grafted_band_is_the_format_that_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction, and the one that was missing.

    Its sibling below overrides a Classic lega to `mantra`, where the grafted band is
    `RosterRules()` — so a branch that had forgotten the conditional entirely and always
    built `RosterRules()` was indistinguishable from the correct one, and a mutation saying
    exactly that survived all 2,218 tests. Overriding a *Mantra* lega to `classic` is where
    the conditional is load-bearing: the wrong graft plans a 30-man Mantra band over a pool
    that has P/D/C/A roles and no schemi.
    """
    reader = _Reader((RosterRules(size=32), SNAPSHOT_DECLARED, "mantra"))
    _patch(monkeypatch, reader)
    said: list[str] = []

    rules, provenance, fmt = _lega_rules(
        4103937, "classic", session=_fake_session, warn=said.append
    )

    assert fmt == "classic"
    assert isinstance(rules, ClassicRosterRules), f"grafted a {type(rules).__name__}"
    assert provenance == ASSUMED_NOTHING
    assert said and "mantra" in said[0] and "--format" in said[0]


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


# -- `--size`: the room's own total, which `asta bid` cannot read for itself ---------------


class TestNoLiveCommandDefaultsTheFormatAtTheRead:
    """The format is *decided* before the world is read, never defaulted at the call.

    `asta room` passed `listone=resolved.asta_type or "mantra"`, and that `or` is how a room
    declaring no format came to be played as Mantra — pool, value model, corpus and the
    11-schemi legality matrix, none of which it had asked for. The coercion also hid the
    guard: it turned an unanswered question into an answer before `build_plan_inputs` could
    refuse it.

    `resolve_room` refuses that room now, so the `or` is dead — and **dead is exactly why
    this is structural.** Restoring it changes no behaviour and no behavioural test can see
    it; it survived a mutation battery. What it does is put the fail-open back, ready for
    the day someone widens `ResolvedRoom.asta_type` to optional again.
    """

    @staticmethod
    def _reads() -> dict[str, object]:
        """`{command: the listone argument, or None when it passes none}`."""
        import ast

        from _paths import module_file

        tree = ast.parse(module_file("fantabot.interface.asta").read_text(encoding="utf-8"))
        found: dict[str, object] = {}
        for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(fn):
                if not (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", None))
                    in ("read_plan_inputs", "build_plan_inputs")
                ):
                    continue
                stated = [k.value for k in node.keywords if k.arg == "listone"]
                found[fn.name] = stated[0] if stated else None
        return found

    def test_the_scan_finds_every_world_read(self) -> None:
        """A scan over nothing passes for ever. Four commands read a world; a fifth that
        starts to — or one of these four that stops — shows up here rather than silently."""
        assert set(self._reads()) == {
            "asta_room", "asta_bid", "asta_calibrate", "asta_bench"
        }

    def test_the_two_live_commands_state_the_format(self) -> None:
        """The two that can spend credits, and the only two that can be told a format:
        `asta room` reads it off the resolved room, `asta bid` off `--format`/`--lega`."""
        reads = self._reads()

        assert reads["asta_room"] is not None and reads["asta_bid"] is not None

    def test_only_the_bench_omits_it(self) -> None:
        """`asta bench` declares no `--format` and replays recorded Mantra rooms, so taking
        the parameter's default is consistent rather than forgetful. Named here so a *second*
        omission is not read as this one.

        `asta calibrate` used to be the other name on this list, and the note here used to
        say its Mantra-only reading was "a separate question". It was not: the recorded
        Classic corpus is the **larger** one — 259 rooms and 32,101 sales at 8x500 against
        48 and 6,466 — and `--ceiling-alpha`'s value rests entirely on that sweep. It now
        states its format on both reads; see `TestTheCalibrationSweepsOneCorpus`.
        """
        reads = self._reads()

        assert set(k for k, v in reads.items() if v is None) == {"asta_bench"}


    def test_none_of_them_falls_back_with_an_or(self) -> None:
        import ast

        offenders = [
            f"{command}: {ast.unparse(arg)}"
            for command, arg in self._reads().items()
            if isinstance(arg, ast.BoolOp)
        ]
        assert offenders == [], (
            f"a world read defaults its own format: {offenders}. The format is decided "
            "before the read — an `or` here answers a question the room never did."
        )


class TestTheCalibrationSweepsOneCorpus:
    """`asta calibrate` grades a corpus against prices read from a corpus. Both must be the
    **same** corpus, and the format is half of what identifies one.

    The command already carried that rule for the room *shape*: `--teams`/`--credits` reach
    `read_plan_inputs` and `recorded_auctions` alike, because a 10x1000 replay graded against
    8x500 prices measures the mismatch rather than the alpha. The format is the same rule and
    was missing from it — both reads took the Mantra default, which agreed, so nothing was
    wrong about the sweep it ran. What was wrong is that no other sweep could be asked for,
    and the unreachable one was the bigger corpus.

    Read from the syntax tree because the defect is an *omitted keyword*: a behavioural test
    passing `--format classic` sees a Classic sweep either way once one keyword is wired, and
    a half-wired command is exactly the failure — a Classic replay priced off Mantra rooms.
    """

    @staticmethod
    def _calls() -> dict[str, Any]:
        import ast as _ast

        from _paths import module_file

        tree = _ast.parse(module_file("fantabot.interface.asta").read_text(encoding="utf-8"))
        fn = next(
            n
            for n in tree.body
            if isinstance(n, _ast.FunctionDef) and n.name == "asta_calibrate"
        )
        found: dict[str, _ast.Call] = {}
        for node in _ast.walk(fn):
            name = getattr(getattr(node, "func", None), "id", None) or getattr(
                getattr(node, "func", None), "attr", None
            )
            if isinstance(node, _ast.Call) and name in ("read_plan_inputs", "recorded_auctions"):
                found[name] = node
        return found

    def test_both_reads_are_present(self) -> None:
        """A scan over one call would pass by finding nothing to disagree with."""
        assert set(self._calls()) == {"read_plan_inputs", "recorded_auctions"}

    def test_the_two_reads_name_the_same_format_expression(self) -> None:
        """Not "both mention a format" — the *same* expression, so one cannot be edited to a
        literal while the other keeps reading the flag."""
        import ast as _ast

        calls = self._calls()
        world = [k.value for k in calls["read_plan_inputs"].keywords if k.arg == "listone"]
        corpus = [k.value for k in calls["recorded_auctions"].keywords if k.arg == "asta_type"]

        assert world and corpus, (
            "one of the two corpus reads takes its format from the default: "
            f"world={bool(world)} corpus={bool(corpus)}"
        )
        assert _ast.unparse(world[0]) == _ast.unparse(corpus[0])

    @pytest.mark.parametrize("bad", ["Classic", "mantar", "", "both"])
    def test_a_format_that_is_neither_is_refused_before_anything_opens(
        self, bad: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And refused *first* — before the database, because the refusal is about the
        argument and needs nothing read to decide it.

        `asta room` and `asta bid` each learned this separately (`RoomRefused`, and the
        `--format` guard); the sweep took a third path and validated nothing. A typo would
        have reached `read_plan_inputs`, whose own guard raises `ValueError` — a traceback
        where the other two print a line, and only after a multi-megabyte read.
        """
        import fantabot.adapters.persistence as persistence
        import fantabot.interface.asta as asta_cli

        def _never(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("a refused format opened the database")

        monkeypatch.setattr(persistence.database_manager, "get_session", _never)

        with pytest.raises(typer.Exit) as refused:
            asta_cli.asta_calibrate(
                alpha=[1.0], teams=8, credits=500, season="2025/26", lam=0.3, fmt=bad
            )

        assert refused.value.exit_code == 2

    @pytest.mark.parametrize(
        ("fmt", "kind", "size"), [("classic", "classic", 25), ("mantra", "mantra", 30)]
    )
    def test_the_band_the_replay_fills_follows_the_format(
        self, fmt: str, kind: str, size: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The third read, and the one no AST scan above covers: `sweep`'s `rules`.

        Its default is `RosterRules()` — Mantra, 30 — so a command that wired `listone` and
        `asta_type` and stopped there would sweep the Classic corpus against a 30-man Mantra
        band. `admits` drops any evening with fewer lots than `rules.size`, so that is not a
        subtly-off number: it silently discards Classic rooms for failing to fill a roster
        Classic does not have, and the two AST tests above would both still pass.
        """
        import fantabot.adapters.persistence as persistence
        import fantabot.adapters.persistence.news_sentiment as news_sentiment
        import fantabot.adapters.persistence.repositories.aste as aste
        import fantabot.application.asta_calibrate as calibrate
        import fantabot.interface.asta as asta_cli

        seen: dict[str, Any] = {}

        @contextlib.contextmanager
        def _session() -> Any:
            yield object()

        class _Repo:
            def __init__(self, _session: Any) -> None: ...

            def recorded_auctions(self, **kwargs: Any) -> list[Any]:
                seen["asta_type"] = kwargs.get("asta_type")
                return []

        def _world(_session: Any, **kwargs: Any) -> Any:
            seen["listone"] = kwargs.get("listone")
            return SimpleNamespace(pool=[], value=None, prices={}, teams={}, legality={})

        def _sweep(_auctions: Any, _alphas: Any, **kwargs: Any) -> list[Any]:
            seen["rules"] = kwargs["rules"]
            return [SimpleNamespace(auctions=0, dropped=0, line=lambda: "")]

        monkeypatch.setattr(persistence.database_manager, "get_session", _session)
        monkeypatch.setattr(news_sentiment, "NewsSentimentSource", lambda _s: object())
        monkeypatch.setattr(asta_cli, "sentiment_rows", lambda *_a, **_k: [])
        monkeypatch.setattr(asta_cli, "read_plan_inputs", _world)
        monkeypatch.setattr(aste, "AsteRepository", _Repo)
        monkeypatch.setattr(calibrate, "sweep", _sweep)

        asta_cli.asta_calibrate(
            alpha=[1.0], teams=8, credits=500, season="2025/26", lam=0.3, fmt=fmt
        )

        assert seen["listone"] == fmt and seen["asta_type"] == fmt
        assert getattr(seen["rules"], "kind", "mantra") == kind
        assert seen["rules"].size == size

    def test_the_format_is_a_name_the_command_declares(self) -> None:
        """And it is the flag, not a literal — a literal would pin the sweep to one corpus
        again, which is the whole defect."""
        import ast as _ast

        calls = self._calls()
        stated = next(k.value for k in calls["read_plan_inputs"].keywords if k.arg == "listone")

        assert isinstance(stated, _ast.Name), f"not a variable: {_ast.unparse(stated)}"

class TestBothCommandsDeclareTheSameDefault:
    """`--format` means "detect it" on both, and the default is what says so.

    `asta optimize` declared `""` and `asta bid` declared `"mantra"`, which reaches
    `_lega_rules` indistinguishable from an operator typing it. So the override branch fired
    on any Classic lega, warned *"planning mantra because --format says so"* about a flag
    nobody passed, and discarded the declared band. Read from the syntax tree, because the
    defect was a *default* — a behavioural test on one command cannot see the other drift.
    """

    @staticmethod
    def _default(command: str) -> object:
        """The declared default of `--format`, found by the flag it declares.

        **Not by index arithmetic.** The first version computed
        `names.index("fmt") - (len(names) - len(defaults))`, which is right only while every
        parameter has a default, and indexes from the *end* when it goes negative — so it
        could read a neighbouring option's default and report it as this one's, silently.
        Matching on the `"--format"` literal inside the `typer.Option` call is
        self-verifying: it cannot pick a different option, and it fails loudly if the flag
        is ever renamed.
        """
        import ast

        from _paths import module_file

        tree = ast.parse(module_file("fantabot.interface.asta").read_text(encoding="utf-8"))
        fn = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == command
        )
        options = [
            node
            for node in ast.walk(fn.args)
            if isinstance(node, ast.Call)
            and any(
                isinstance(arg, ast.Constant) and arg.value == "--format"
                for arg in node.args
            )
        ]
        assert len(options) == 1, f"{command} declares {len(options)} --format options"
        return ast.literal_eval(options[0].args[0])

    def test_the_helper_reads_the_option_it_names(self) -> None:
        """The meta-check. Index arithmetic over a parameter list is exactly the shape that
        reports a neighbour's value and looks right; this asserts the commands give answers
        that are not all the same, so a helper returning one constant fails.

        **Re-anchored on `asta_calibrate`.** It used to pair `asta_bid` against
        `asta_live`, which stopped distinguishing anything the moment `asta live` learned to
        detect and moved to `""` — the assertion would have gone vacuous while still
        passing. `asta calibrate` is the right partner and not a substitute of convenience:
        it names a *corpus* to sweep rather than a room to read, so it has nothing to detect
        from and states `mantra` on purpose.
        """
        assert self._default("asta_bid") == ""
        assert self._default("asta_calibrate") == "mantra"
        assert len({self._default(c) for c in ("asta_bid", "asta_calibrate")}) == 2

    def test_asta_bid_detects_like_asta_optimize(self) -> None:
        assert self._default("asta_bid") == self._default("asta_optimize") == "", (
            "one command treats its own default as an override the operator typed"
        )

    def test_asta_live_detects_too(self) -> None:
        """This asserted `"mantra"`, under the title *"asta live states a format because it
        can detect nothing"*. The premise was wrong and it was load-bearing.

        `asta live` takes no `--lega` and never reaches `_lega_rules` — both true — but it
        takes `--league`, which names a FantaLab room that declares its own `asta_type`,
        and that id also joins the harvested corpus. Two things to detect from, not none.
        So the default is `""` like the other detecting commands, and a `--league` run where
        neither rung answers is **refused** rather than priced as Mantra.

        `asta calibrate` keeps `"mantra"` and is now what pins the helper above: it names a
        corpus, which really does have nothing to detect from.
        """
        assert self._default("asta_live") == ""

    def test_the_replay_path_keeps_the_stated_default(self) -> None:
        """The detection is about `--league`. A replay's landing row is `{seen_at,
        auction_id, state}` and the `auction/<fl>` state carries no format, so `--replay`
        resolves `fmt or "mantra"` in the body — asserted here as source, because an option
        default of `""` would otherwise read as if replays were being refused too.
        """
        import ast as _ast

        from _paths import module_file

        tree = _ast.parse(module_file("fantabot.interface.asta").read_text(encoding="utf-8"))
        fn = next(
            n for n in tree.body if isinstance(n, _ast.FunctionDef) and n.name == "asta_live"
        )
        fallbacks = [
            _ast.unparse(node)
            for node in _ast.walk(fn)
            if isinstance(node, _ast.BoolOp) and "mantra" in _ast.unparse(node)
        ]

        assert fallbacks == ["fmt or 'mantra'"], f"the replay fallback moved: {fallbacks}"


class TestTheSizeOverride:
    """`asta bid` is unauthenticated. `--size` is how the room's own band reaches it.

    Without it the child plans and caps on whatever `--lega` says — and `--lega` defaults to
    `settings.fantabot_league_id`, a *leghe.fantacalcio* id with no relation to the FantaLab
    room. Measured on this machine: that lega's last sync declares **32** players, so a room
    declaring 25 was planned as a 32-man roster (unbuyable — the `1.14` failure, *"cannot
    complete the roster"*), and a room declaring 32 while the lega said 25 would have capped
    **7 credits too loose**.
    """

    def test_no_size_leaves_the_lega_band_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reader = _Reader((RosterRules(size=32, min_goalkeepers=2, min_movement=23), "x", "mantra"))
        _patch(monkeypatch, reader, configured=4103937)

        rules, provenance, _fmt = _lega_rules(0, "", session=_session, warn=_warn, size=0)

        assert rules.size == 32 and provenance == "x"

    def test_a_size_overrides_the_total_and_says_where_it_came_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fourth provenance, not a reuse of `ROOM_DECLARED`: an operator's flag and a
        room's own statement are different facts, and 1.8's column would lie about the
        source. The repo already keeps three apart for exactly this reason."""
        from fantabot.domain.asta.state import OPERATOR_DECLARED

        reader = _Reader((RosterRules(size=32, min_goalkeepers=2, min_movement=23), "x", "mantra"))
        _patch(monkeypatch, reader, configured=4103937)

        rules, provenance, _fmt = _lega_rules(0, "", session=_session, warn=_warn, size=25)

        assert rules.size == 25
        assert provenance == OPERATOR_DECLARED

    def test_the_band_it_produces_is_coherent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole reason it goes through `resize_band`: a bare `size=` would give -3
        keepers over the built-in band."""
        reader = _Reader((RosterRules(), "x", "mantra"))
        _patch(monkeypatch, reader, configured=4103937)

        rules, _provenance, _fmt = _lega_rules(0, "", session=_session, warn=_warn, size=25)

        assert rules.max_goalkeepers() >= rules.min_goalkeepers
        assert (rules.size, rules.min_movement) == (25, 23)

    def test_it_applies_to_the_built_in_band_when_there_is_no_lega(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The app's own path: it passes no `--lega` at all, because the FantaLab room is
        not a lega. The size must still land."""
        reader = _Reader((RosterRules(), "unused", "mantra"))
        _patch(monkeypatch, reader, configured=0)

        rules, provenance, fmt = _lega_rules(0, "mantra", session=_session, warn=_warn, size=25)

        assert rules.size == 25
        assert provenance == "given with --size"
        assert reader.asked == [], "a size that needs no lega must not open the database"
        assert fmt == "mantra"

    def test_a_named_lega_keeps_its_floors_and_only_the_total_moves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator who names a lega **and** a size meant both.

        The branch keys on `lega`, the parameter, not on `resolved` — which folds in
        `settings.fantabot_league_id`. `asta bid`'s `--format` always has a value, so a
        condition reading `fmt` collapsed to "any stated size skips the read", and a
        terminal `asta bid --lega 4103937 --size 25` silently threw away the floors of the
        lega it had just been told to use.
        """
        from fantabot.domain.asta.state import OPERATOR_DECLARED

        reader = _Reader((RosterRules(size=32, min_goalkeepers=2, min_movement=23), "x", "mantra"))
        _patch(monkeypatch, reader, configured=4103937)

        rules, provenance, _fmt = _lega_rules(
            4103937, "mantra", session=_session, warn=_warn, size=25
        )

        assert reader.asked == [4103937], "a named lega must still be read"
        assert (rules.size, rules.min_movement) == (25, 23)
        assert provenance == OPERATOR_DECLARED

    def test_an_unnamed_lega_is_not_read_just_because_env_has_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The app's path. It sends no `--lega` because a FantaLab room is not a lega, and
        `settings.fantabot_league_id` is somebody's *leghe.fantacalcio* default — reading it
        would hand the child another league's role floors under the room's own total."""
        reader = _Reader((RosterRules(size=32, min_goalkeepers=2, min_movement=23), "x", "mantra"))
        _patch(monkeypatch, reader, configured=4103937)

        rules, _provenance, _fmt = _lega_rules(0, "mantra", session=_session, warn=_warn, size=25)

        assert reader.asked == [], "an unnamed lega was read anyway"
        assert (rules.size, rules.min_movement) == (25, 23)

    def test_a_size_survives_an_override_that_drops_the_band(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The override discards the *lega's* band, not the operator's own number.

        `--format` disagreeing with the lega means the declared band describes a different
        game and cannot be planned on — so it falls back to the built-in one. `--size` is a
        separate statement about this room, and returning before applying it left
        `--lega X --size 25 --format <mismatch>` planning and capping on 30. Silently: the
        warning is about the format, and says nothing about the size being dropped.
        """
        from fantabot.domain.asta.state import OPERATOR_DECLARED

        reader = _Reader((ClassicRosterRules(), "x", "classic"))
        said = _patch(monkeypatch, reader, configured=3584692)

        rules, provenance, fmt = _lega_rules(
            3584692, "mantra", session=_session, warn=said.append, size=25
        )

        assert rules.size == 25, "the stated size was dropped with the lega's band"
        assert provenance == OPERATOR_DECLARED
        assert fmt == "mantra"
        assert said and "because --format says so" in said[0]

    def test_a_refused_size_is_the_commands_to_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`resize_band` raises `ValueError`; turning that into an exit code is the Typer
        body's half, and `_lega_rules` stays a function anything can call."""
        reader = _Reader((RosterRules(), "x", "mantra"))
        _patch(monkeypatch, reader, configured=0)

        with pytest.raises(ValueError):
            _lega_rules(0, "mantra", session=_session, warn=_warn, size=1)


def _session() -> object:
    return object()


def _warn(_note: str) -> None:
    return None
