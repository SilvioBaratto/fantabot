"""The launchd job that fields the weekly lineup with nobody at the keyboard.

S5 of the scheduled-lineup agreement (2026-09-11): the bot submits lega 4103937's lineup by
itself, several times a day, and no run touches a lineup after the matchday has started.
S1-S4 built the switch, the cutoff, the run record and the history page. This is the part
that makes any of it fire.

**Writing a plist and loading it are two different acts, and this module only does the
first.** ``install`` never invokes ``launchctl`` — it writes the file and hands back the
``launchctl bootstrap`` line for the operator to run. That is the same shape as the rest of
the repository's arming discipline: ``FANTABOT_AUTO_ACT`` and ``--arm`` are two opt-ins
because the person who edits ``.env`` in the morning is not the one at the keyboard at
21:47, and a plist that bootstrapped itself would quietly be a third lock nobody turned.

Three things in the rendered job are decisions rather than plumbing.

**The program is ``sys.executable``**, not ``fantabot-app`` and not a shell line. launchd
starts a job with almost no environment: no ``PATH`` worth the name, no login shell, and
therefore no ``conda activate``. The interpreter running the install is the one that has
both packages importable, so it is the one written into the plist.

**The working directory is the repository**, because ``fantabot.config`` builds its
``Settings`` relative to the process's cwd — that is how ``.env`` is found at all, and it
is the same footgun ``harvest_dir`` documents for ``FANTABOT_HARVEST_DIR``. A job pointed
anywhere else runs hourly and refuses every time, so a missing ``.env`` is refused here,
where someone is watching, rather than at 02:00.

**The logs are under ``~/.fantabot/logs``**, on the internal disk. The repository lives on
an external SSD; if it is unmounted launchd cannot start the job at all, and the only
evidence available is what launchd itself writes — which has to land somewhere still
mounted. ``status`` reports that case by name for the same reason.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fantabot_app import paths

__all__ = [
    "INTERVAL_S",
    "LABEL",
    "LOADING_VERBS",
    "Installed",
    "Job",
    "ScheduleRefused",
    "Status",
    "bootstrap_line",
    "build_job",
    "install",
    "interpreter_record_path",
    "launchctl",
    "loading_calls",
    "plist_path",
    "read_interpreter_record",
    "record_interpreter",
    "render",
    "require_darwin",
    "resolve_interpreter",
    "run_command",
    "status",
    "uninstall",
]

#: The launchd job name. Reverse-DNS by convention, and the plist is named after it because
#: `launchctl bootout` addresses the label while `bootstrap` addresses the file.
LABEL = "com.fantabot.lineup"

#: Hourly. `--scheduled` makes a run past kickoff a no-op that exits 0, so the cost of
#: firing often is a line in the run record, and the benefit is that a Mac awake for any
#: hour of the matchday morning fields the lineup.
INTERVAL_S = 3600

#: `launchctl print` on a label that is not loaded. Not an error — it is the answer.
NOT_LOADED_CODE = 113

Launchctl = Callable[[Sequence[str]], int]


class ScheduleRefused(Exception):
    """The job was not written, or not addressed. Raised before anything is changed."""


def _platform() -> str:
    """This machine's platform, as one seam the tests move rather than patching `sys`."""
    return sys.platform


def _uid() -> int:
    """This user's uid, as the second seam — and it refuses rather than raising.

    `domain_target` and `bootstrap_line` called `os.getuid()` directly, which does not
    exist on Windows. Every test in this suite simulates macOS by moving `_platform`, so
    `require_darwin` was satisfied and the next line raised `AttributeError: module 'os'
    has no attribute 'getuid'` — 22 of `app-ci`'s 23 Windows failures, every one of them an
    internal error from a module whose whole design is to refuse by name.

    A seam rather than a conditional, for the reason `_platform` is one: the suite has to
    be able to *complete* its simulation. Faking the platform and not the platform's API is
    what made this invisible until a Windows runner ran the suite.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise ScheduleRefused(
            f"launchd addresses a job as gui/<uid>/<label>, and {_platform()} has no uids. "
            "launchd is macOS only; on another platform use that platform's scheduler to "
            "run `fantabot lineup submit --arm --scheduled` from the repository."
        )
    return int(getuid())


def require_darwin() -> None:
    """launchd is macOS's, and says so rather than failing three calls later.

    The command is registered on every platform so ``--help`` stays honest, and the
    refusal names the platform it is on — which is also what keeps ``app-ci``'s Windows
    job green without an import-time guard.
    """
    current = _platform()
    if current != "darwin":
        raise ScheduleRefused(
            f"the scheduled lineup is a launchd job and this is {current}. "
            "launchd is macOS only; on another platform use that platform's scheduler to "
            "run `fantabot lineup submit --arm --scheduled` from the repository."
        )


def launchctl(argv: Sequence[str]) -> int:
    """Run ``launchctl`` for real, and **swallow its output**.

    Only the exit code is a fact here — ``print`` on an unloaded label is 113, which is the
    answer to "is it loaded", not an error. Left inherited, launchctl writes its own
    "Could not find service" and "Boot-out failed: 3: No such process" to stderr *above* the
    line this app prints to say the same thing calmly, and the operator reads two
    contradictory messages about one state.

    ``run_command`` below deliberately does the opposite: the submit's output is what lands
    in the launchd log, and is the only record of an unattended run's reasoning.
    """
    return subprocess.run(list(argv), check=False, capture_output=True).returncode


def run_command(command: Sequence[str], cwd: str | None = None) -> int:
    """Run the submit and return its exit code. Output is inherited, so launchd logs it."""
    return subprocess.run(list(command), cwd=cwd, check=False).returncode


@dataclass(frozen=True)
class Job:
    """One launchd job, fully resolved. Every path is absolute; nothing is looked up later."""

    label: str
    working_dir: Path
    league: int
    arm: bool
    python: str
    interval_s: int
    stdout: Path
    stderr: Path

    @property
    def program(self) -> list[str]:
        """What launchd executes. ``--arm`` is present or absent, never a value.

        A lock spelled ``--arm=false`` is one edit from open and reads as armed at a
        glance; the repository's two locks are both opt-in flags for that reason.
        """
        argv = [
            self.python,
            "-m",
            "fantabot_app.cli",
            "schedule",
            "run",
            "--league",
            str(self.league),
        ]
        if self.arm:
            argv.append("--arm")
        return argv


def build_job(
    *,
    working_dir: Path,
    league: int,
    arm: bool = True,
    label: str = LABEL,
    interval_s: int = INTERVAL_S,
    python: str | None = None,
) -> Job:
    """Resolve a job, refusing a working directory that cannot carry the settings.

    ``.env`` is not decoration here: ``FANTABOT_AUTO_ACT`` lives in it, and without it an
    armed job refuses on every run — a bot that looks alive and fields nothing.
    """
    root = Path(working_dir).expanduser().resolve()
    if not (root / ".env").is_file():
        raise ScheduleRefused(
            f"no .env in {root}: the scheduled run reads FANTABOT_AUTO_ACT from the "
            "repository's .env, relative to its working directory. Pass --working-dir "
            "pointing at the repository root."
        )
    if league <= 0:
        raise ScheduleRefused(
            "no lega id: pass --league, or set FANTABOT_LEAGUE_ID before installing. "
            "A plist carrying neither would refuse once an hour."
        )
    logs = paths.logs()
    return Job(
        label=label,
        working_dir=root,
        league=league,
        arm=arm,
        python=python or sys.executable,
        interval_s=interval_s,
        stdout=logs / f"{label}.out.log",
        stderr=logs / f"{label}.err.log",
    )


def interpreter_record_path(label: str = LABEL) -> Path:
    """Where the granted interpreter is recorded: ``~/.fantabot/<label>.interpreter.json``.

    Beside the logs, on the internal disk, for the same reason they are: the repository is
    on an external SSD, and a record that cannot be read when that disk is missing goes
    quiet exactly when the job does.

    Not a key in the plist. launchd owns that file's schema, and an unknown key there is a
    warning in a log nobody reads; this is the app's own record and it belongs in the app's
    own home.
    """
    return paths.home() / f"{label}.interpreter.json"


def resolve_interpreter(program: str) -> Path | None:
    """What macOS actually exec's for *program*, or ``None`` when nothing is there.

    The distinction is the whole reason this is a function. ``Path.resolve()`` does not
    raise on a dangling symlink — it returns the target it could not find — so "the
    interpreter moved" and "the interpreter is gone" resolve to the same shape, and they
    are different failures with different fixes: one is a lost Full Disk Access grant and a
    job that **hangs**, the other is a job launchd refuses to start at all.
    """
    resolved = Path(program).expanduser().resolve()
    return resolved if resolved.exists() else None


def record_interpreter(job: Job) -> Path:
    """Write down which binary the Full Disk Access grant has to be made on.

    The plist names ``app/.venv/bin/python3``; TCC attributes the grant to whatever is
    actually exec'd, which is the uv-managed CPython that symlink points at. Nothing in the
    repository recorded that path, so a ``uv python`` upgrade moved it and the job went back
    to hanging with no output to explain why — the symptom is a run record that stops
    appearing, and there was nothing to compare against.
    """
    target = interpreter_record_path(job.label)
    target.parent.mkdir(parents=True, exist_ok=True)
    resolved = resolve_interpreter(job.python)
    body = {
        "program": job.python,
        "resolved": str(resolved) if resolved is not None else None,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return target


def read_interpreter_record(label: str = LABEL) -> Path | None:
    """The recorded binary, or ``None`` when the job predates this record.

    Unrecorded is **not** drift, and the two must not be conflated: one is fixed by re-running
    ``schedule install``, the other by a dialog in System Settings. Reporting the first as
    the second sends the operator to a grant that changes nothing.
    """
    target = interpreter_record_path(label)
    if not target.is_file():
        return None
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    resolved = body.get("resolved") if isinstance(body, dict) else None
    return Path(str(resolved)) if resolved else None


def plist_path(label: str = LABEL) -> Path:
    """Where launchd reads the job from: ``~/Library/LaunchAgents/<label>.plist``."""
    return paths.launch_agents() / f"{label}.plist"


def render(job: Job) -> bytes:
    """The plist launchd parses. ``RunAtLoad`` is what catches up after a reboot.

    launchd does not replay the ``StartInterval`` ticks that passed while the machine was
    off — it fires once on wake and then resumes the interval. Without ``RunAtLoad`` a Mac
    booted at 10:00 on a matchday would wait until 11:00 to field anything.
    """
    body: dict[str, object] = {
        "Label": job.label,
        "ProgramArguments": job.program,
        "WorkingDirectory": str(job.working_dir),
        "StartInterval": job.interval_s,
        "RunAtLoad": True,
        "StandardOutPath": str(job.stdout),
        "StandardErrorPath": str(job.stderr),
        "ProcessType": "Background",
    }
    return plistlib.dumps(body)


#: The ``launchctl`` verbs that put a job into launchd. ``install`` may call **none** of
#: them; it may only read. Bootstrapping puts a bot on a live platform with real credits,
#: and that stays a keystroke the operator types.
#:
#: Written as a set rather than as "install calls launchctl never", which is what the
#: guard used to say. That was the right rule stated one notch too wide, and it cost a
#: real defect: unable to ask launchd anything, ``install`` printed *"Nothing is scheduled
#: yet"* over a job that was loaded, ARMED and 11 runs in. Same split
#: ``tests/test_layers.py`` makes for ``apileague`` — reads stay legal, writes never.
#:
#: ``bootout`` is deliberately absent: ``uninstall`` calls it, and removing a job is the
#: opposite of starting one.
LOADING_VERBS = frozenset({"bootstrap", "kickstart", "load", "enable", "start", "submit"})


def loading_calls(calls: Iterable[Sequence[str]]) -> list[list[str]]:
    """Every recorded ``launchctl`` call that would put the job into launchd.

    Pure, and matching whole words: ``/Users/me/start/x.plist`` is a path, not a ``start``.
    """
    return [list(call) for call in calls if any(word in LOADING_VERBS for word in call)]


@dataclass(frozen=True)
class Installed:
    """What ``install`` did, and what launchd already had — two separate facts.

    Conflating them is the defect this type exists for. A plist on disk and a job in
    launchd are different things, which is the whole premise of ``install`` loading
    nothing, so a command that reports only the first can claim nothing is scheduled while
    a bidder runs hourly.
    """

    path: Path
    #: Whether launchd already had this label when ``install`` ran.
    loaded: bool
    #: Whether the bytes on disk changed. ``False`` means the definition launchd was given
    #: is the one now on disk, so there is nothing to apply. A claim about the **file** and
    #: not about launchd's in-memory copy, which cannot be read back — an operator who
    #: edited the plist by hand after bootstrapping is outside what this can know.
    changed: bool


def install(job: Job, *, launchctl: Launchctl, uid: int | None = None) -> Installed:
    """Write the plist, then **ask** launchd what it already has. **Load nothing.**

    The read is what makes the command able to say something true. Without it the three
    states below are one message, and the wrong one was printed to a live armed job:

    * **not loaded** — the state ``install`` is designed to leave behind; the operator's
      ``launchctl bootstrap`` is what changes it.
    * **loaded, unchanged** — nothing to do. The operator's own case on 2026-09-20, when
      ``install`` was re-run only to record the Full Disk Access grant.
    * **loaded, changed** — launchd goes on running the previous definition until it is
      told otherwise, so this one needs ``bootout`` and then ``bootstrap``.
    """
    paths.launch_agents().mkdir(parents=True, exist_ok=True)
    job.stdout.parent.mkdir(parents=True, exist_ok=True)
    target = plist_path(job.label)
    body = render(job)
    before = target.read_bytes() if target.is_file() else None
    target.write_bytes(body)
    record_interpreter(job)
    return Installed(
        path=target,
        # A read. `print` exits 0 for a loaded label and 113 with "Could not find service"
        # otherwise, which is the answer and not a failure — `status` makes the same call.
        loaded=launchctl(["launchctl", "print", domain_target(job.label, uid=uid)]) == 0,
        changed=before != body,
    )


def domain_target(label: str = LABEL, *, uid: int | None = None) -> str:
    """The per-user domain launchd addresses a job in: ``gui/<uid>/<label>``."""
    return f"gui/{_uid() if uid is None else uid}/{label}"


def bootstrap_line(path: Path, *, uid: int | None = None) -> str:
    """The line the operator runs to make the job live. Printed, never executed here."""
    return f"launchctl bootstrap gui/{_uid() if uid is None else uid} {path}"


@dataclass(frozen=True)
class Status:
    """What is on disk and what launchd is actually running — two separate facts.

    A plist that was never bootstrapped is the state ``install`` leaves behind on purpose,
    so conflating the two would report an unarmed machine as live.
    """

    plist: Path
    installed: bool
    loaded: bool
    working_dir: Path | None
    working_dir_readable: bool
    league: int | None
    armed: bool | None
    #: The interpreter as the plist names it — normally a venv symlink.
    interpreter: Path | None = None
    #: What that name resolves to now, or `None` when nothing is there any more.
    interpreter_resolved: Path | None = None
    #: What `install` recorded, which is the path the Full Disk Access grant is on.
    #: `None` for a job installed before this was recorded — unrecorded, not drifted.
    interpreter_recorded: Path | None = None
    #: The recorded grant no longer describes what launchd will exec. The job does not
    #: fail: a CPython refused by TCC **hangs**, so this is the only available warning.
    interpreter_drifted: bool = False


def status(*, launchctl: Launchctl, label: str = LABEL, uid: int | None = None) -> Status:
    """Read the plist back and ask launchd whether it is loaded."""
    target = plist_path(label)
    if not target.is_file():
        return Status(
            plist=target,
            installed=False,
            loaded=False,
            working_dir=None,
            working_dir_readable=False,
            league=None,
            armed=None,
        )
    body = plistlib.loads(target.read_bytes())
    argv: list[str] = list(body.get("ProgramArguments", []))
    working_dir = Path(str(body.get("WorkingDirectory", "")))
    league: int | None = None
    if "--league" in argv:
        candidate = argv[argv.index("--league") + 1]
        league = int(candidate) if candidate.isdigit() else None
    program = argv[0] if argv else None
    resolved = resolve_interpreter(program) if program else None
    recorded = read_interpreter_record(label)
    return Status(
        plist=target,
        installed=True,
        loaded=launchctl(["launchctl", "print", domain_target(label, uid=uid)]) == 0,
        working_dir=working_dir,
        working_dir_readable=working_dir.is_dir(),
        league=league,
        armed="--arm" in argv,
        interpreter=Path(program) if program else None,
        interpreter_resolved=resolved,
        interpreter_recorded=recorded,
        # An unrecorded job cannot have drifted — there is nothing it drifted from.
        interpreter_drifted=recorded is not None and resolved != recorded,
    )


def uninstall(*, launchctl: Launchctl, label: str = LABEL, uid: int | None = None) -> str:
    """Boot the job out, **then** remove the plist. Returns ``removed`` or ``not_installed``.

    The order is load-bearing: ``bootout`` addresses the job by label and launchd resolves
    that label through the file. Unlink first and the job keeps running with nothing left
    to name it — the state the operator reaches by deleting the plist by hand.
    """
    target = plist_path(label)
    if not target.is_file():
        return "not_installed"
    launchctl(["launchctl", "bootout", domain_target(label, uid=uid)])
    target.unlink()
    # A record outliving its job would report drift on a machine with no job at all.
    interpreter_record_path(label).unlink(missing_ok=True)
    return "removed"
