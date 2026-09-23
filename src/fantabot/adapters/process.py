"""Run a child command in its own process group, and take the group down together.

The hourly refresh runs as a **child** and not in-process, for three reasons that are all
about the submit:

* it imports the agent SDK and the whole persistence stack, which the `indexcompare` submit
  is guarded from loading at all (AD3);
* it talks to a live site and to an LLM, either of which can hang in a way no `try` catches;
* it is the optional half. A refresh that wedged in-process would hold the job past its next
  hourly tick, and the tick after that is a matchday.

**Its own session, so the kill is a group kill.** `start_new_session=True` makes the child a
process-group leader whose pgid equals its pid, and the refresh itself spawns children — the
voti scrape's, the agent's. Killing the child alone leaves those orphaned and running, which
is the shape of the leak this repository has already paid for once: a SIGINT-ignoring child
left 151 processes behind, because pytest reaps directories and not processes.

**SIGTERM, then SIGKILL.** A refresh in the middle of an upsert should be allowed to finish
the statement; one that ignores the signal is not going to start. The grace between them is
short because the hard timeout has already expired — this is the second ask, not the first.

⚠ **Waiting on the child is not waiting on the group, and the escalation asks the group.**
The first version escalated only when the *child* outlived SIGTERM — so a refresh whose own
grandchild ignored the signal left it running while the leader died on cue, and the runner
reported a clean kill. Measured 2026-09-23: one stray survived every run.

⚠ **The pgid is captured at spawn, not derived from the pid afterwards.** `start_new_session`
makes them equal, and once the leader has been reaped `os.getpgid` raises — so a probe that
looked the group up through the leader would report an empty group the moment the leader
died, which is precisely the case it exists to catch.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Sequence
from contextlib import suppress

#: Seconds between the polite signal and the one that cannot be refused.
DEFAULT_GRACE_SECONDS = 5.0


def run_grouped(
    command: Sequence[str],
    *,
    timeout: float,
    grace: float = DEFAULT_GRACE_SECONDS,
    cwd: str | None = None,
) -> tuple[int | None, str]:
    """`(exit code, note)`. `None` and a note when the group had to be killed.

    Never raises for the child's sake: a child that could not even be started is a note, the
    same as one that ran too long. The caller is a job whose real work is already done.
    """
    try:
        child = subprocess.Popen(
            list(command),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
        )
    except OSError as exc:
        return None, f"could not start: {type(exc).__name__}"

    # `start_new_session` makes the child a group leader, so its pid *is* the pgid — and
    # capturing it now is the only way to still have it once the leader has been reaped.
    pgid = child.pid
    try:
        return child.wait(timeout=timeout), ""
    except subprocess.TimeoutExpired:
        pass

    note = f"killed after {timeout:.0f}s"
    signal_group(pgid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        child.wait(timeout=grace)
    if group_alive(pgid):
        # The leader may be long gone; what is left is whatever it started. SIGKILL cannot
        # be refused, and the reap below is unbounded because a timeout would leave the
        # zombie the parent never collects.
        signal_group(pgid, signal.SIGKILL)
        note = f"{note} (SIGTERM ignored)"
    with suppress(subprocess.TimeoutExpired):
        child.wait(timeout=grace)
    return None, note


def signal_group(pgid: int, sig: signal.Signals) -> None:
    """Signal a whole group by its id, tolerating one that has already gone.

    `killpg`, not `kill`: the child's own children are what a hung refresh leaves behind.
    """
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        return


def group_alive(pgid: int) -> bool:
    """Whether **anything** in the group is still running.

    Signal 0 asks without sending. Taking the pgid rather than a pid is the point: after the
    leader is reaped, looking the group up through it raises, and a probe that did so would
    report every abandoned grandchild as gone.
    """
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True
