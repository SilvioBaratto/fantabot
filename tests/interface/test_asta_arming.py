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
    one command end to end; this proves the shape for every command, including `asta room`,
    which has no end-to-end harness.

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
    assert (
        isinstance(arm, ast.Subscript)
        and isinstance(arm.value, ast.Name)
        and arm.value.id == "armed"
    ), f"{command}'s writer reads `{ast.unparse(arm)}` — only `armed[0]` is what a Ctrl-C clears"
