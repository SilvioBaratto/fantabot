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

**The addressee is the role lock, not a pid.** A run killed between the write and its own
shutdown leaves *exit* on disk, and a child that read it would quit at startup for ever,
for a reason that expired. The first version addressed the flag to the child's pid to fix
that. It was wrong twice over.

It did not work: on Windows the supervisor wrote `disarm` for pid 2300 and the child
polling that same file never matched it — recorded in `app-ci` run 34112470790, where the
job log holds both `polling` and `stopping: disarm flag for pid 2300` and no `saw
disarm`. Whether `Popen.pid` and the child's own `os.getpid()` are the same number across
a spawn from a uv venv there was never established, and a stop mechanism whose identity
scheme cannot be verified on the platform it exists for is not a mechanism.

And it was solving a problem already solved. `lock.py` guarantees **one holder per
(landing zone, role)**, which is exactly the identity the pid was standing in for — so
the flag is named `<landing>.<role>.stop`, the same shape as the lock file, and whoever
holds the lock owns the flag. Collector and loader are the *intended* pairing on one
landing zone, so the role has to be in the name or a stop aimed at one would stop the
other.

Staleness is then the holder's own business: a run **clears the flag as it starts**. It
holds the lock, so nothing else can be relying on what it erases, and no request written
before it began was written for it.

**No database, and no `signal`.** This module is polled from the collection path, so it
carries `lock.py`'s rule: nothing here may reach persistence, or a database outage could
stop a collector. The `signal` ban is the other half — importing it here would quietly
make the portable half of the stop platform-dependent again.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from fantabot.adapters.files.lock import ROLES

#: Stop deciding, keep drawing. The first stage of the two-stage gesture.
DISARM: Final = "disarm"
#: Leave. The second stage, and the last one this file has.
EXIT: Final = "exit"

#: In escalation order. `request_stop` walks it and stops at the end.
STAGES: Final = (DISARM, EXIT)


def stop_path(base: Path, role: str) -> Path:
    """`<base>.<role>.stop` — beside the lock file it shadows, and named the same way.

    Beside it for `lock.py`'s reason: `ls` in the harvest home should answer "what is
    happening here", and a flag filed somewhere central would not be part of that answer.
    Derived from *base* so two landing zones never share one, and from *role* because a
    collector and a loader on one landing zone are the intended pairing — one file for
    both would let a stop aimed at either stop the other.
    """
    if role not in ROLES:
        raise ValueError(f"{role!r} is not a role. Use one of: {', '.join(ROLES)}")
    return base.with_name(f"{base.name}.{role}.stop")


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


def read_stop(path: Path) -> str | None:
    """The stage requested of whoever holds this role, or `None`.

    `None` for every case that is not a request: no flag, an unreadable one, and one
    naming a stage this module does not have.
    """
    state = _read(path).get("state")
    return state if state in STAGES else None


def request_stop(path: Path) -> str:
    """Ask whoever holds this role to stop, one stage further than last time.

    Returns the stage now in force. The escalation is read-then-write rather than a
    counter held by the caller, so the two stages survive the caller restarting: the app
    can be reloaded between an operator's two clicks and the second still means *exit*.

    Escalation cannot run away past a dead run because the *holder* clears the flag as it
    starts — see `clear_stop`. Without that, a run that died at *exit* would make the next
    run's first click an exit, landing the old run's second gesture on a process that
    never saw the first.
    """
    current = read_stop(path)
    state = EXIT if current is not None else DISARM
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": state}), encoding="utf-8")
    return state


def clear_stop(path: Path) -> None:
    """Forget any request. Not an error when there is none — that is the ordinary case.

    Called twice by every run that honours this flag: **once as it starts**, which is what
    makes a dead run's leftover harmless, and once as it leaves. The first call is the
    load-bearing one and it is safe because the caller holds the role lock, so nothing
    else can be waiting on what it erases.
    """
    path.unlink(missing_ok=True)


#: How often a waiter looks. Short enough that a stop feels immediate on the one evening
#: it is used, long enough that the poll costs nothing across three hours: 12 stats a
#: minute against a file the OS has cached.
POLL_S: Final = 5.0


async def wait_for_stop(
    path: Path,
    *,
    sleep: Callable[[float], Awaitable[None]],
    poll_s: float = POLL_S,
) -> str:
    """Block until a stop is requested of this role, then return the stage.

    The flag is checked **before** the first sleep, so a request that landed while the
    caller was still starting up is not held for a whole poll interval.

    `sleep` is injected for the reason every clock in this repository is: a test that
    waited the real cadence would either be slow or be a race. It takes the same shape as
    `Supervisor`'s, so both can be handed `asyncio.sleep` at the one call site that has a
    running loop.
    """
    while True:
        state = read_stop(path)
        if state is not None:
            return state
        await sleep(poll_s)
