"""A stop a running loop asks for, rather than one delivered to it.

**Why this is a file.** The stop the app already had was a signal, and on Windows there is
no portable one. `CTRL_BREAK_EVENT` — the only thing that reaches a process group there —
arrives at a Python child as `SIGBREAK` and terminates it *before* `except
KeyboardInterrupt` runs. Measured on the runner: `exited 3221225786`, which is
`STATUS_CONTROL_C_EXIT`, with the child's own shutdown line never printed, which also
makes the SIGKILL escalation behind it unreachable. That is not a Windows quirk to work
around one call at a time; it is the whole delivery model being wrong for a child that has
work to finish. A polled file inverts it: the child chooses when to look, so its shutdown
path always runs, and it runs identically on all three platforms.

Two further properties come free and are the reason to prefer this even on POSIX. The flag
**survives the process that wrote it**, so an app that dies mid-stop does not leave a
child that will never be asked again. And it is **writable by anyone with the path**,
which is what makes a browser tab able to ask — the tab is not the child's parent and
cannot signal it.

**Two states, because the gesture has two stages.** *Disarm* means stop deciding and keep
drawing; *exit* means leave. That is the contract `asta bid` documents for its two Ctrl-Cs
and the reason this is not a boolean. There is deliberately no stage after *exit*: a third
request stays there, and a caller that wants more escalates by killing the process, which
is a different mechanism on purpose.

**The flag is addressed, and that is what keeps it from latching.** A run killed between
the write and its own shutdown leaves *exit* on disk. A child reading a bare state would
then quit at startup, for ever, for a reason that expired. So the flag names the pid it is
for, and a reader whose own pid does not match sees nothing.

That is not the pid-file scheme `lock.py` argues against, and the difference is which
question the pid is asked. `lock.py` refuses to answer *"is a collector running?"* from a
recorded pid, because pid 40122 may since have become a browser — an inference about
someone else, made from a stale record. Here the pid is an **addressee**: the only reader
that acts is the process whose own `os.getpid()` matches, and that is a fact it knows
rather than infers. The worst a reused pid can do is deliver one expired *disarm* to a
process that will then clear it.

**No database, and no `signal`.** This module is polled from the collection path, so it
carries `lock.py`'s rule: nothing here may reach persistence, or a database outage could
stop a collector. The `signal` ban is the other half — importing it here would quietly
make the portable half of the stop platform-dependent again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

#: Stop deciding, keep drawing. The first stage of the two-stage gesture.
DISARM: Final = "disarm"
#: Leave. The second stage, and the last one this file has.
EXIT: Final = "exit"

#: In escalation order. `request_stop` walks it and stops at the end.
STAGES: Final = (DISARM, EXIT)


def stop_path(base: Path) -> Path:
    """`<base>.stop` — beside the thing it stops, and derived from it.

    Beside it for `lock.py`'s reason: `ls` in the harvest home should answer "what is
    happening here", and a flag filed somewhere central would not be part of that answer.
    Derived from *base* rather than fixed so two landing zones never share one flag, which
    would let a stop aimed at one stop the other.
    """
    return base.with_name(f"{base.name}.stop")


def _read(path: Path) -> dict[str, object]:
    """The flag as written, or `{}` for anything that is not a flag.

    Every failure funnels to the same empty answer, deliberately. A missing file, a
    half-written one, a hand-edited one and a directory in the way are all *the absence of
    a request*. A flag that could raise would let a corrupt byte stop a collector, which
    is the failure this whole module exists to prevent — in a new costume.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def read_stop(path: Path, *, pid: int) -> str | None:
    """The stage requested of the run with this *pid*, or `None`.

    `None` for every other case: no flag, an unreadable one, one naming a stage this
    module does not have, and — the one that matters — one addressed to a different run.
    """
    flag = _read(path)
    if flag.get("pid") != pid:
        return None
    state = flag.get("state")
    return state if state in STAGES else None


def request_stop(path: Path, *, pid: int) -> str:
    """Ask the run with this *pid* to stop, one stage further than last time.

    Returns the stage now in force. The escalation is read-then-write rather than a
    counter held by the caller, so the two stages survive the caller restarting: the app
    can be reloaded between an operator's two clicks and the second still means *exit*.

    Escalation is **per run**. A flag left by a dead run at *exit* does not make this
    run's first request an exit — it is replaced, not advanced, or the old run's second
    click would land on a process that never saw the first.
    """
    # `read_stop` already returns `None` for anything that is not this run's own valid
    # stage, so "there is a request" and "escalate" are the same condition.
    current = read_stop(path, pid=pid)
    state = EXIT if current is not None else DISARM
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "state": state}), encoding="utf-8")
    return state


def clear_stop(path: Path) -> None:
    """Forget any request. Not an error when there is none — that is the ordinary case."""
    path.unlink(missing_ok=True)
