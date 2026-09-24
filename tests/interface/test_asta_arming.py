"""Two locks before a credit is spent, and the second one is a flag.

`FANTABOT_AUTO_ACT` is read inside `place_raise` at call time and comes from `.env`, so
flipping it arms **every** invocation at once, for the rest of the process's life and every
process after it. That is one lock, and it is the wrong shape for a command that spends real
money: the operator who flips it in the morning is not necessarily the one who runs the
command at 21:47.

So `--arm` is a *positive* flag, defaulting off. Forgetting it means watching. The failure
mode of an opt-*out* flag is spending money, which is the asymmetry that decides which way
round this goes.

`asta room` will grow the same pair (T12); `asta bid` needs it first because it is the only
command that can bid tonight.
"""

from __future__ import annotations

from typing import Any

from fantabot.interface.asta import bid_writer


def _send(payload: dict[str, Any]) -> str:
    return f"SENT {payload['price']}"


class TestBothLocksMustBeOpen:
    def test_armed_only_when_the_env_and_the_flag_agree(self) -> None:
        assert bid_writer(auto_act=True, arm=True, send=_send)({"price": 7}) == "SENT 7"

    def test_the_flag_alone_sends_nothing(self) -> None:
        outcome = bid_writer(auto_act=False, arm=True, send=_send)({"price": 7})

        assert outcome != "SENT 7"
        assert outcome.sent is False
        assert outcome.dry_run is True

    def test_the_env_alone_sends_nothing(self) -> None:
        """The one that matters: `.env` says true, the operator did not ask."""
        outcome = bid_writer(auto_act=True, arm=False, send=_send)({"price": 7})

        assert outcome.sent is False
        assert outcome.dry_run is True

    def test_neither_sends_nothing(self) -> None:
        assert bid_writer(auto_act=False, arm=False, send=_send)({"price": 7}).sent is False


class TestADisarmedWriteIsWellFormed:
    """`run_bid_loop` reads `.sent` off whatever comes back and counts the bid from it.

    Returning `None` would read as `sent=False` through `getattr`, which is accidentally
    right and would stop being right the moment the loop looks at anything else — the
    price it thought it bid, or the node it bid on.
    """

    def test_it_carries_the_price_it_would_have_sent(self) -> None:
        assert bid_writer(auto_act=False, arm=False, send=_send)({"price": 42}).price == 42

    def test_a_malformed_price_does_not_raise(self) -> None:
        """Mirrors `place_raise`, which coerces rather than trusting the payload."""
        assert bid_writer(auto_act=False, arm=False, send=_send)({}).price == 0
        assert bid_writer(auto_act=False, arm=False, send=_send)({"price": True}).price == 0

    def test_it_names_the_node_it_would_have_written(self) -> None:
        outcome = bid_writer(auto_act=False, arm=False, send=_send, node="assign")

        assert outcome({"price": 1}).node == "assign"


# -- the disarm: first Ctrl-C stops bidding, second exits (3.9a) ------------------------

import contextlib  # noqa: E402 — grouped with the tests that need them
import signal  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402


class TestTheTwoStageInterrupt:
    """One handler, shared by both live commands.

    `asta room` had it inline; `asta bid` never had it at all. Two copies of "the live loop"
    drifted and the one that lost the disarm is the one that spends credits — the plan's own
    words: *"The command that spends credits is the one that cannot be disarmed."*

    The handler is called directly rather than signalled at the process, for the reason
    `test_news_fetch_write.py` gives: that is exactly what Ctrl-C delivers, and a test that
    signals its own runner is a test that can kill the suite.
    """

    def test_the_first_interrupt_disarms_and_the_second_exits(self) -> None:
        from fantabot.interface.asta import _disarm_on_sigint

        armed = [True]
        with _disarm_on_sigint(armed):
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler) and handler is not signal.default_int_handler

            handler(signal.SIGINT, None)
            assert armed == [False], "the first Ctrl-C must stop bidding"

            with pytest.raises(KeyboardInterrupt):
                handler(signal.SIGINT, None)

    def test_the_flag_clearing_armed_does_not_spend_a_ctrl_c(self) -> None:
        """**The sibling of `stop_poll`'s own defect, in the other direction.**

        Two mechanisms clear one `armed` list, and on POSIX `ProcessJob.stop` writes the
        flag *before* it signals. If the child's `keep_going` polls in that window it clears
        `armed[0]`, and the `SIGINT` that follows then finds `False` — reads it as a
        **second** Ctrl-C and ends an armed run on stage one.

        So the handler counts its own interrupts instead of inferring the count from a list
        somebody else writes to. The keyboard contract is unchanged and that is the point:
        once disarms, twice exits.
        """
        from fantabot.interface.asta import _disarm_on_sigint

        armed = [True]
        with _disarm_on_sigint(armed):
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            # The flag half, honoured by the loop between the write and the signal.
            armed[0] = False

            handler(signal.SIGINT, None)  # the SIGINT half of that same one click

            assert armed == [False]
            # Still running: one request, one stage. The *next* interrupt is the second.
            with pytest.raises(KeyboardInterrupt):
                handler(signal.SIGINT, None)

    def test_a_run_that_was_never_armed_exits_on_the_first(self) -> None:
        """Nothing to disarm, so the first Ctrl-C means what it always meant."""
        from fantabot.interface.asta import _disarm_on_sigint

        with _disarm_on_sigint([False]):
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            with pytest.raises(KeyboardInterrupt):
                handler(signal.SIGINT, None)

    def test_the_previous_handler_comes_back_even_when_the_run_raises(self) -> None:
        """`asta room` restored it on the happy path only — the line after the loop, not a
        `finally` — so a loop that raised left its handler installed for the process."""
        from fantabot.interface.asta import _disarm_on_sigint

        before = signal.getsignal(signal.SIGINT)
        with pytest.raises(RuntimeError), _disarm_on_sigint([True]):
            raise RuntimeError("the loop died")

        assert signal.getsignal(signal.SIGINT) is before

    def test_off_the_main_thread_it_installs_nothing_and_the_run_still_happens(self) -> None:
        """Python refuses a signal handler off the main thread. That costs the graceful
        disarm and nothing else — refusing to run the room over it is the worse trade."""
        from fantabot.interface.asta import _disarm_on_sigint

        before = signal.getsignal(signal.SIGINT)
        ran: list[bool] = []
        failed: list[BaseException] = []

        def body() -> None:
            try:
                with _disarm_on_sigint([True]):
                    ran.append(True)
            except BaseException as exc:
                failed.append(exc)

        worker = threading.Thread(target=body)
        worker.start()
        worker.join()

        assert ran == [True] and failed == []
        assert signal.getsignal(signal.SIGINT) is before


def _wire_asta_bid(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Everything between `asta bid`'s entry and its loop, faked. Returns what was sent.

    No socket, no database: the bridge, the plan inputs, the band, the tracker, the journal
    and the router are all stand-ins. What is real is the command body itself — the part
    that decides whether a Ctrl-C can reach the writer.
    """
    from fantabot import config
    from fantabot.adapters.files import room_journal
    from fantabot.adapters.http.fantalab import listone
    from fantabot.adapters.http.fantalab.rtdb import BidOutcome
    from fantabot.adapters.persistence import database_manager, news_sentiment
    from fantabot.application import asta_room, asta_session
    from fantabot.domain.asta.state import RosterRules
    from fantabot.interface import asta

    # Both locks open: the ambient one through `live_auto_act`'s exported branch.
    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true")

    monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 1})
    monkeypatch.setattr(listone, "cache_age", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(listone, "is_stale", lambda *_a, **_k: False)
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(news_sentiment, "NewsSentimentSource", lambda _s: None)
    monkeypatch.setattr(asta, "sentiment_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(
        asta,
        "read_plan_inputs",
        lambda *_a, **_k: SimpleNamespace(
            pool=["p1"], value={}, prices={}, teams={}, legality=None, names={}
        ),
    )
    monkeypatch.setattr(
        asta, "_lega_rules", lambda _lega, fmt, **_k: (RosterRules(), "a test band", fmt)
    )
    monkeypatch.setattr(asta_room, "RoomTracker", lambda **_k: SimpleNamespace())
    monkeypatch.setattr(
        room_journal,
        "RoomJournal",
        lambda _p: SimpleNamespace(write=lambda _row: None, close=lambda: None),
    )

    sent: list[int] = []

    class _Router:
        node = "auction"

        def __init__(self, **_k: Any) -> None:
            pass

        def write_raise(self, payload: dict[str, Any]) -> Any:
            sent.append(int(payload["price"]))
            return BidOutcome(
                price=payload["price"], node="auction", dry_run=False, sent=True, status=200
            )

    # Patched at the factory, not the class. Since 3.11 the router is built in
    # `application/asta_session.lot_router` — binding `place_raise` to a shard is a
    # write, and the T-spine rule kept it on the ratchet until it moved out of the
    # Typer body. A patch on the adapter class is inert from here.
    monkeypatch.setattr(asta_session, "lot_router", lambda _db, _league: _Router())
    return sent


class TestAstaBidCanBeDisarmedMidRun:
    """3.9a's acceptance: *"an armed `asta bid` is disarmable mid-run from the terminal."*

    It bound `bid_writer(arm=arm)` from a plain bool captured once, and installed no SIGINT
    handler — every `signal.signal` in `interface/asta.py` was inside `asta_room`. So Ctrl-C
    went straight to `run_bid_loop`'s `except KeyboardInterrupt` and ended the run: no
    "stop bidding, keep watching", on the one command that places real raises.
    """

    def test_the_first_ctrl_c_holds_the_next_bid_and_the_second_ends_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fantabot.adapters.http.fantalab.room import LoopReport
        from fantabot.application import asta_session
        from fantabot.interface.app import app

        sent = _wire_asta_bid(monkeypatch)
        seen: dict[str, Any] = {}

        def loop(**kw: Any) -> LoopReport:
            write = kw["write"]
            seen["first"] = write({"price": 7}).sent

            handler = signal.getsignal(signal.SIGINT)
            seen["installed"] = callable(handler) and handler is not signal.default_int_handler
            if not seen["installed"]:
                # Calling Python's default handler would raise a real `KeyboardInterrupt`
                # through the runner and abort the whole pytest session.
                return LoopReport(cycles=1, bids_sent=1, refused={})

            handler(signal.SIGINT, None)  # the first Ctrl-C
            seen["second"] = write({"price": 8}).sent
            try:
                handler(signal.SIGINT, None)  # the second
            except KeyboardInterrupt:
                seen["exited"] = True  # what `run_bid_loop` does with it: return the report
            return LoopReport(cycles=2, bids_sent=1, refused={})

        # Patched where the name is *looked up*, not where it is defined. Since 3.9a the
        # Typer body drives `AstaSession.run`, which holds its own `run_bid_loop` reference —
        # so a patch on the adapter module is inert and this harness would run the real loop,
        # whose `keep_going` is `lambda _cycle: True`. Every assertion below is unchanged:
        # what moved is the call, not what the command does with a Ctrl-C.
        monkeypatch.setattr(asta_session, "run_bid_loop", loop)
        before = signal.getsignal(signal.SIGINT)

        result = CliRunner().invoke(
            app,
            ["asta", "bid", "--league", "L1", "--db", "1", "--team", "T1", "--user", "U1",
             "--arm"],
        )

        assert seen.get("installed"), "asta bid installed no interrupt handler: Ctrl-C cannot disarm it"
        assert result.exit_code == 0, result.output
        assert seen["first"] is True, "an armed run must bid before anyone interrupts it"
        assert seen["second"] is False, "a bid was sent after the operator disarmed"
        assert sent == [7], f"only the pre-interrupt bid may reach the room, sent {sent}"
        assert seen.get("exited") is True, "the second Ctrl-C must end the run"
        assert "disarmed" in result.output
        assert signal.getsignal(signal.SIGINT) is before, "the handler was left installed"


# -- every live command, structurally ----------------------------------------------------

import ast  # noqa: E402

from _paths import module_file  # noqa: E402


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


_ASTA = ast.parse(module_file("fantabot.interface.asta").read_text(encoding="utf-8"))

def _loop_call(fn: ast.FunctionDef) -> ast.Call | None:
    """The call that drives this command's bid loop, found by what it *takes*.

    Not by the callee's name. Since 3.6b `asta room` drives `AstaSession.run` and `asta bid`
    still calls `run_bid_loop` directly, so a scan keyed to either name covers one command
    and reports success over the other — the exact failure mode `test_the_discovery_finds_
    both_live_commands` exists to catch. What both drivers take, and nothing else in this
    module does, is a `write=` that is a **lambda**: the per-bid writer. That is also the
    property under test, so the discriminator and the assertion are the same fact.
    """
    found = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and any(
            keyword.arg == "write" and isinstance(keyword.value, ast.Lambda)
            and any(
                isinstance(inner, ast.Call) and _name(inner.func) == "bid_writer"
                for inner in ast.walk(keyword.value)
            )
            for keyword in node.keywords
        )
    ]
    return found[0] if len(found) == 1 else None


#: Every command body that runs a bid loop — discovered, not listed, so a third live command
#: is covered the day it is written rather than the day someone remembers to add it here.
LIVE_COMMANDS = sorted(
    fn.name
    for fn in _ASTA.body
    if isinstance(fn, ast.FunctionDef) and _loop_call(fn) is not None
)


def test_the_discovery_finds_both_live_commands() -> None:
    """Pinned, so a rename cannot leave the parametrised test below with zero cases —
    which pytest reports as nothing failing."""
    assert LIVE_COMMANDS == ["asta_bid", "asta_room"]


@pytest.mark.parametrize("command", LIVE_COMMANDS)
def test_every_live_command_can_be_disarmed(command: str) -> None:
    """The loop runs inside `_disarm_on_sigint`, and the writer reads `armed[0]` per bid.

    This is the check that would have caught `asta bid`: no handler around its loop, and a
    writer reading a bool captured at start. Read from the syntax tree rather than the text,
    for the reason `scripts/verify_criteria.py` gives — a substring check cannot tell a call
    from a sentence about a call. `TestAstaBidCanBeDisarmedMidRun` proves the behaviour for
    one command end to end; this proves the shape for every command. `asta room` used to
    have no harness at all, which is what made the structural form load-bearing here — it
    now has one (`_run_asta_room`), but only as far as the banner: nothing drives its loop,
    so the Ctrl-C claim is still read from the syntax tree for both commands.

    The lift does not weaken it: `bid_writer` is the gate both locks meet at, it stays in
    `interface/` (3.9a's own note), and whichever call it is handed to has to sit inside the
    handler.
    """
    fn = next(n for n in _ASTA.body if isinstance(n, ast.FunctionDef) and n.name == command)
    loop = _loop_call(fn)
    assert loop is not None

    guarded = [
        w
        for w in ast.walk(fn)
        if isinstance(w, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and _name(item.context_expr.func) == "_disarm_on_sigint"
            for item in w.items
        )
        and any(n is loop for n in ast.walk(w))
    ]
    # "Outside the shared handler", not "cannot be disarmed": an inline handler can disarm
    # too — `asta room`'s did — and an inline copy is how `asta bid` came to have none.
    assert guarded, (
        f"{command} runs its bid loop outside `_disarm_on_sigint`, the one handler both "
        "live commands share"
    )

    write = next(k.value for k in loop.keywords if k.arg == "write")
    assert isinstance(write, ast.Lambda), (
        f"{command} builds its writer once, at loop start: {ast.unparse(write)[:90]}"
    )
    [arm] = [
        k.value
        for n in ast.walk(write)
        if isinstance(n, ast.Call) and _name(n.func) == "bid_writer"
        for k in n.keywords
        if k.arg == "arm"
    ]
    # **Contains `armed[0]`, rather than *is* it.** The property under test is that a Ctrl-C
    # reaches the writer, and `armed[0]` is the only thing a Ctrl-C clears — so it has to be
    # read here. Since the stop flag became the whole stop on Windows the expression is
    # `armed[0] and read_stop(stop_flag) is None`, which reads the list *and* what can have
    # changed since the top of the cycle. An equality assertion would have forbidden the
    # stronger condition, which is a test written against a shape rather than a claim.
    reads_armed = [
        node
        for node in ast.walk(arm)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "armed"
    ]
    assert reads_armed, (
        f"{command}'s writer reads `{ast.unparse(arm)}` — only `armed[0]` is what a Ctrl-C clears"
    )
    assert not isinstance(arm, ast.Constant), (
        f"{command}'s writer arms on the constant `{ast.unparse(arm)}`"
    )


# -- the dry-run line names EVERY shut lock, not the first one ---------------------------


from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402


def _run_asta_bid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *args: str,
    auto_act: bool,
    bridge_age: float = 0.0,
) -> str:
    """`asta bid`, driven as far as its banner and no further. Returns what it printed.

    The loop is replaced by one that does nothing, because everything this section is about
    is printed *before* the first poll — deliberately, so an operator can tell an armed run
    from a rehearsal at a glance.

    `bridge_age` is a real age fed through `listone.cache_age`, and the real `is_stale` is
    put back over `_wire_asta_bid`'s stub — `asta_session` imported that function by name, so
    a patch on the adapter module is inert against `room_arming` and leaving the stub in
    place would make the *old* code unable to reach its stale branch at all. The staleness
    rule itself is pinned in `tests/adapters/http/test_fantalab_listone.py`.

    `journal_path` is redirected into `tmp_path`. The journal object itself is faked, but the
    stop flag is derived from that path and is written and cleared for real, and the default
    resolves under `./data` — which is the repository.
    """
    from fantabot import config
    from fantabot.adapters.http.fantalab import listone
    from fantabot.adapters.http.fantalab.room import LoopReport
    from fantabot.application import asta_session
    from fantabot.interface.app import app

    _wire_asta_bid(monkeypatch)
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true" if auto_act else "false")
    monkeypatch.setattr(config, "journal_path", lambda: tmp_path / "room.jsonl")
    monkeypatch.setattr(listone, "cache_age", lambda *_a, **_k: bridge_age)
    monkeypatch.setattr(listone, "is_stale", asta_session.is_stale)
    monkeypatch.setattr(
        asta_session,
        "run_bid_loop",
        lambda **_k: LoopReport(cycles=0, bids_sent=0, refused={}),
    )

    result = CliRunner().invoke(
        app,
        ["asta", "bid", "--league", "L1", "--db", "1", "--team", "T1", "--user", "U1", *args],
    )
    assert result.exit_code == 0, result.output
    return result.output


#: `asta room` takes the room as a positional argument and `parse_room_url` accepts a bare
#: fantaleague id, so no link has to be spelled out here.
_ROOM_URL = "11111111-2222-3333-4444-555555555555"


class _NoLive:
    """`rich.live.Live` with the alternate screen taken out.

    The real one switches the terminal to its alternate buffer and paints there; under
    `CliRunner` that writes control sequences into the very transcript these assertions read.
    Nothing this section is about is drawn inside the `Live` — the banner is printed above
    it, deliberately, so it survives the first paint.
    """

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _NoLive:
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        pass


def _wire_asta_room(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Everything between `asta room`'s entry and its live view, faked.

    Harder to stand up than `asta bid`'s: that command is unauthenticated by design, while
    this one opens the encrypted store, resolves the room over an authenticated fetch, and
    then takes the screen. Every one of those is reached through a seam the module looks up
    at call time — the body's imports are all inside it — so the fakes go on the modules and
    nothing here touches a socket, a database or a real key.

    What stays real is the part under test: `room_arming`, `Arming.because`,
    `_arming_sentences` and the body that prints them.
    """
    import rich.live

    from fantabot import config
    from fantabot.adapters.files import room_journal
    from fantabot.adapters.http.fantalab import listone, rest
    from fantabot.adapters.http.fantalab.room import LoopReport
    from fantabot.adapters.persistence import database_manager, news_sentiment
    from fantabot.adapters.tokens import fantalab_store
    from fantabot.application import asta_room, asta_session
    from fantabot.domain.tokens import crypto
    from fantabot.interface import asta

    # The ambient lock through `live_auto_act`'s exported branch, as `_wire_asta_bid` does.
    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true")
    monkeypatch.setattr(config, "journal_path", lambda: tmp_path / "room.jsonl")

    # The door: a cipher that needs no key, a store that needs no database, a fetcher that
    # needs no bearer. `TokenCipher` is faked rather than fed a key because a real one would
    # be a credential in a test file.
    monkeypatch.setattr(crypto, "TokenCipher", lambda _key: object())
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(
        fantalab_store,
        "FantalabStore",
        lambda _session, _cipher: SimpleNamespace(
            load=lambda: SimpleNamespace(user_id="U1")
        ),
    )
    monkeypatch.setattr(rest, "fetcher_from", lambda _store: (lambda *_a, **_k: {}))
    monkeypatch.setattr(
        asta_room,
        "resolve_room",
        lambda _fantaleague_id, **_k: SimpleNamespace(
            fantaleague_id=_ROOM_URL,
            db=1,
            seat=SimpleNamespace(team_name="T1", fantateam_id="F1"),
            num_teams=8,
            num_credits=500,
            budget=500.0,
            # No room-declared band: `rules_for_room` is left real and answers
            # `ASSUMED_NOTHING`, which is what 153 of 247 recorded rooms actually declare.
            number_of_players_selection=None,
            min_goalkeepers=None,
            min_others=None,
            players_settings_data=None,
            asta_type="mantra",
            asta_mode="ASTA",
            raise_mode="RIALZO",
        ),
    )

    monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 1})
    monkeypatch.setattr(listone, "cache_age", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(news_sentiment, "NewsSentimentSource", lambda _s: None)
    monkeypatch.setattr(asta, "sentiment_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(
        asta,
        "read_plan_inputs",
        lambda *_a, **_k: SimpleNamespace(
            pool=["p1"], value={}, prices={}, teams={}, legality=None, names={}, roles={}
        ),
    )
    monkeypatch.setattr(
        room_journal,
        "RoomJournal",
        lambda _p: SimpleNamespace(write=lambda _row: None, close=lambda: None),
    )
    monkeypatch.setattr(
        asta_session,
        "lot_router",
        lambda _db, _league: SimpleNamespace(
            node="auction", read_lot=lambda: (None, None), write_raise=lambda _p: None
        ),
    )
    # Patched at the factory for `_wire_asta_bid`'s reason: the session is composed in
    # `application/`, and a patch on the loop the session holds would be inert from here.
    monkeypatch.setattr(
        asta_session,
        "session_for",
        lambda **_k: SimpleNamespace(
            run=lambda **_kk: LoopReport(cycles=0, bids_sent=0, refused={})
        ),
    )
    monkeypatch.setattr(rich.live, "Live", _NoLive)


def _run_asta_room(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *args: str,
    auto_act: bool,
    bridge_age: float = 0.0,
) -> str:
    """`asta room`, driven as far as its banner. Returns what it printed.

    `--no-copilot`: the LLM pane is a daemon thread, and a test that starts one is a test
    that can outlive itself. `input="y\\n"` answers the confirmation an *armed* run asks
    before it takes the screen — which is this command's ARMED banner, and is echoed into
    the transcript rather than stubbed away.
    """
    from fantabot import config
    from fantabot.adapters.http.fantalab import listone
    from fantabot.interface.app import app

    _wire_asta_room(monkeypatch, tmp_path)
    monkeypatch.setenv(config.AUTO_ACT_VAR, "true" if auto_act else "false")
    monkeypatch.setattr(listone, "cache_age", lambda *_a, **_k: bridge_age)

    result = CliRunner().invoke(
        app, ["asta", "room", _ROOM_URL, "--no-copilot", *args], input="y\n"
    )
    assert result.exit_code == 0, result.output
    return result.output


#: How to drive each live command as far as its banner, keyed by the *discovered* command
#: names. `test_every_live_command_has_a_banner_harness` is what makes the discovery pay:
#: a third live command fails it the day it is written, rather than the day somebody
#: remembers this file exists.
#:
#: `asta room` had no harness at all until now. Every `CliRunner` invocation of it in the
#: repository — `tests/interface/test_options.py:66,73,80,88` — passes a `nan`/`inf` option
#: and aborts inside parameter validation, so its banner had never been printed by a test.
_LIVE_RUNNERS: dict[str, Callable[..., str]] = {
    "asta_bid": _run_asta_bid,
    "asta_room": _run_asta_room,
}

#: What an **armed** run of each command says in place of the banner. They differ on purpose
#: and the difference is not cosmetic: `asta bid` prints a red ARMED line and starts polling,
#: `asta room` asks the operator to confirm the spend before the live view takes the screen.
_ARMED_MARKER = {
    "asta_bid": "● ARMED — bids are real credits.",
    "asta_room": "Bid REAL CREDITS in",
}


def test_every_live_command_has_a_banner_harness() -> None:
    """The parametrised class below is only as wide as this mapping, and a parametrisation
    with a missing case is a test that reports nothing failing."""
    assert set(_LIVE_RUNNERS) == set(LIVE_COMMANDS)
    assert set(_ARMED_MARKER) == set(LIVE_COMMANDS)


#: The banner's own opening, and the anchor everything below is asserted against.
#:
#: The three sentences this section is about are **not** unique to the banner. In exactly the
#: runs driven here, `interface/asta.py:1368` (`asta bid`) and `:923` (`asta room`) print
#: `listone bridge: refresh failed, using a 5.0h old cache`, and `:1458`/`:957` print
#: `arming refused: listone bridge is 5.0h old...` — so `"listone bridge" in output` was
#: satisfied twice over by lines that are not the line under test. Measured: emptying the
#: third lock's sentence to `{**CLI_SENTENCES, STALE_BRIDGE: ""}` left `asta bid` printing
#: `DRY RUN — nothing will be sent ()` — the exact banner
#: `test_an_armed_run_prints_no_reason_at_all` calls worse than the ternary was — and all
#: 22 tests in this file still passed.
_DRY_RUN = "DRY RUN — nothing will be sent"


def _squashed(output: str) -> str:
    """The transcript as one logical line, so a Rich wrap cannot hide a sentence from a
    substring check.

    Rich wraps the Console at 80 columns under `CliRunner` and wraps at spaces, so collapsing
    every run of whitespace reassembles what was printed. Used for the two lines that carry
    the *measured* bridge age and the operator's own `--max-bridge-age-hours`: both are long
    enough that they are always split, which is why neither had ever been asserted.
    """
    return " ".join(output.split())


def _dry_run_banner(output: str) -> str | None:
    """A live command's dry-run banner as one logical line, or `None` when it printed none.

    Reassembled rather than read off `splitlines()`, because Rich wraps the Console at 80
    columns under `CliRunner` and three shut locks do not fit: the banner arrives as two
    physical lines, the first ending in a trailing space mid-sentence. Closing on the
    parenthesis balance rather than on a line count keeps the terminal width out of the
    assertions — a reason that grows a fourth lock still reassembles.
    """
    lines = output.splitlines()
    heads = [i for i, line in enumerate(lines) if line.startswith(_DRY_RUN)]
    if not heads:
        return None
    assert len(heads) == 1, f"more than one dry-run banner in:\n{output}"
    banner = ""
    for line in lines[heads[0] :]:
        banner = f"{banner} {line.strip()}".strip()
        if banner.count("(") and banner.count("(") == banner.count(")"):
            return banner
    raise AssertionError(f"the dry-run banner never closes its reason:\n{output}")


def _dry_run_reason(output: str) -> str:
    """What the banner gives **inside its parentheses**, and nothing else on screen.

    Every assertion below reads this instead of the transcript, so a sentence has to be in
    the line that explains the dry run rather than merely somewhere above it. That is the
    whole difference between this section and the one that passed under the mutant.
    """
    banner = _dry_run_banner(output)
    assert banner is not None, f"no dry-run banner in:\n{output}"
    tail = banner[len(_DRY_RUN) :].strip()
    assert tail.startswith("(") and tail.endswith(")"), (
        f"the dry-run banner no longer carries a parenthesised reason: {banner!r}"
    )
    return tail[1:-1]


@pytest.mark.parametrize("command", LIVE_COMMANDS)
class TestEveryLiveCommandNamesEveryShutLock:
    """Both live commands reported fewer shut locks than they knew about.

    `asta bid` reported one cause of two, over a ternary:

        why = "--arm not given" if auto_act_now else "FANTABOT_AUTO_ACT is false"

    `application/arming.py` was written to remove that exact line — its docstring quotes it —
    and shipped `Arming.closed` / `Arming.because`. The fix reached `interface/lineup.py` and
    not the two commands that spend real credits.

    **`asta room` was worse and lasted longer.** It computed `gate.closed`, read it for the
    `STALE_BRIDGE` membership test, and printed none of it; and when the banner did arrive,
    the only thing holding it to *every* lock was
    `test_both_live_commands_render_the_same_arming_vocabulary`, which asserts that a call to
    `because` exists and cannot tell "prints every shut lock" from "prints the first of
    them". Measured: appending `.split(" and ")[0]` to the reason in `interface/asta.py:969`
    left all 22 tests in this file green. So the body is parametrised over the **discovered**
    live commands rather than written once for the one that happened to have a harness.
    """

    @staticmethod
    def _run(
        command: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *args: str,
        auto_act: bool,
        bridge_age: float = 0.0,
    ) -> str:
        return _LIVE_RUNNERS[command](
            monkeypatch, tmp_path, *args, auto_act=auto_act, bridge_age=bridge_age
        )

    def test_both_shut_locks_are_named_in_one_line(
        self, command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The whole point. The ternary passes any test that asserts *a* reason is printed,
        so this asserts **both** — an operator who fixes one and retries must not be sent
        back for the other. Asserted of the banner's reason, which is what "in one line"
        claims and what the transcript cannot tell you."""
        reason = _dry_run_reason(self._run(command, monkeypatch, tmp_path, auto_act=False))

        assert "FANTABOT_AUTO_ACT is false" in reason
        assert "--arm not given" in reason

    def test_only_the_lock_that_is_actually_shut_is_named(
        self, command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The other half: naming both unconditionally would pass the test above and send an
        operator to edit a `.env` that is already right.

        The negative stays against the whole transcript on purpose — it is strictly stronger
        there, and the sentence must not appear anywhere, banner or not."""
        output = self._run(command, monkeypatch, tmp_path, auto_act=True)

        assert "--arm not given" in _dry_run_reason(output)
        assert "FANTABOT_AUTO_ACT is false" not in output

    def test_a_stale_bridge_does_not_report_itself_as_a_missing_arm(
        self, command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The lie the two halves told together, and the reason the third lock had to move
        into `room_arming` here too.

        The body cleared `arm` itself when the listone bridge was too old, and the ternary
        then read that cleared value: an operator who typed `--arm`, with
        `FANTABOT_AUTO_ACT=true`, was told `--arm not given`. Both facts about the run were
        correct and the sentence was false.

        This run prints `listone bridge` on two other lines — the stale-cache note and the
        arming refusal, both carrying the measured age. Neither is the banner, and asserting
        the substring of the transcript is how the emptied sentence survived.
        """
        output = self._run(
            command, monkeypatch, tmp_path, "--arm", auto_act=True, bridge_age=5 * 3600
        )

        assert "listone bridge" in _dry_run_reason(output)
        assert "--arm not given" not in output
        assert "FANTABOT_AUTO_ACT is false" not in output

    def test_the_refusal_names_the_measured_age_and_the_operators_own_limit(
        self, command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The two lines the banner's short sentence deliberately does not carry.

        `_arming_sentences` gives the bridge a fixed sentence — "the listone bridge is too
        old to arm on" — precisely because no static map can hold the *measured* age or the
        operator's `--max-bridge-age-hours`. These two lines are the only place either
        number appears, and until now deleting either print was silent in both commands:
        every existing assertion about them was `"listone bridge" in output`, which the
        banner alone satisfies.

        Asserted of the squashed transcript because both sentences are wider than the 80
        columns Rich gives a `CliRunner` Console.
        """
        output = _squashed(
            self._run(
                command, monkeypatch, tmp_path, "--arm", auto_act=True, bridge_age=5 * 3600
            )
        )

        assert "listone bridge: refresh failed, using a 5.0h old cache" in output
        assert (
            "arming refused: listone bridge is 5.0h old, over the 4h limit "
            "(--max-bridge-age-hours). Watching only." in output
        )

    def test_all_three_shut_locks_are_named_at_once(
        self, command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Three causes, one line. `Arming.closed` is a tuple for exactly this case — and
        `one line` is the claim, so the three are asserted of one reassembled reason rather
        than of three places in the transcript."""
        reason = _dry_run_reason(
            self._run(command, monkeypatch, tmp_path, auto_act=False, bridge_age=5 * 3600)
        )

        assert "FANTABOT_AUTO_ACT is false" in reason
        assert "--arm not given" in reason
        assert "listone bridge" in reason

    def test_an_armed_run_prints_no_reason_at_all(
        self, command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`because` returns `""` when nothing is shut, and a "DRY RUN ()" banner on an
        armed run would be worse than the ternary was.

        The positive half is each command's own armed signal, which is not the same line in
        both: the point is that an operator can tell an armed run from a rehearsal before
        the first poll, not that the two commands say it identically.
        """
        output = self._run(command, monkeypatch, tmp_path, "--arm", auto_act=True)

        assert _ARMED_MARKER[command] in output
        assert _dry_run_banner(output) is None, "an armed run printed a dry-run banner"


def test_both_live_commands_render_the_same_arming_vocabulary() -> None:
    """One map, both commands — `asta room` computed `gate.closed` and printed none of it.

    Read from the syntax tree for the reason the section above gives: a substring check
    cannot tell a call from a sentence about a call. The claim is narrow and is the one that
    drifted — each live command hands its `Arming` to `because`, rather than re-deciding the
    wording in its own body.

    ⚠ **Narrow is the word.** This finds that a call to `because` exists; it cannot tell
    "prints every shut lock" from "prints the first of them", because the mutant keeps the
    call. Measured: `gate.because(_arming_sentences()).split(' and ')[0]` in
    `interface/asta.py:969` passed this and all 21 other tests in the file. What closes that
    is `TestEveryLiveCommandNamesEveryShutLock`, which drives each command to its banner and
    reads the reason; this test is the cheap structural half and is kept as one.
    """
    rendered = {
        command
        for command in LIVE_COMMANDS
        for fn in _ASTA.body
        if isinstance(fn, ast.FunctionDef) and fn.name == command
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and _name(node.func) == "because"
    }
    assert rendered == set(LIVE_COMMANDS), (
        f"{sorted(set(LIVE_COMMANDS) - rendered)} decides a dry-run reason in its own body "
        "instead of asking `Arming.because` — which is how one of them came to report one "
        "shut lock of two, and the other to report none"
    )
