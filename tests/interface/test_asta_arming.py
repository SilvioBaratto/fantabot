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
