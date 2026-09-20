"""`fantabot-app schedule` — the launchd job that fields the lineup without an operator.

**Nothing here talks to `launchctl`.** The runner is injected, and the central property of
`install` is that it calls it *zero* times: writing a plist is reversible, loading one puts
a bot on a real platform with real credits. The `launchctl bootstrap` line is printed for
the operator to run, and the test that matters asserts the spy stayed empty.

The three facts a scheduled run depends on, each pinned here rather than discovered at
02:00 on a matchday: the interpreter is **this** one (`sys.executable`, not a PATH lookup —
launchd inherits almost no environment and `conda activate` is not available to it), the
working directory is the repository so `.env` resolves, and the logs are under
`~/.fantabot/logs` — the internal disk, not the external SSD the repository lives on, so a
dead SSD leaves a message rather than silence.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantabot_app import paths, schedule
from fantabot_app.cli import app

runner = CliRunner()


class Spy:
    """A `launchctl` that records instead of running. Returns whatever it is told to."""

    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        return self.code


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private `~` — this suite never writes into the operator's real LaunchAgents."""
    monkeypatch.setattr(paths, "home", lambda: tmp_path / ".fantabot")
    monkeypatch.setattr(paths, "launch_agents", lambda: tmp_path / "Library" / "LaunchAgents")
    monkeypatch.setattr(schedule, "_platform", lambda: "darwin")
    return tmp_path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A working directory that looks like the repository: it has a `.env`."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("FANTABOT_AUTO_ACT=true\n")
    return root


def _job(repo: Path, **kw: object) -> schedule.Job:
    kw.setdefault("league", 4103937)
    return schedule.build_job(working_dir=repo, **kw)  # type: ignore[arg-type]


class TestTheJob:
    def test_the_program_is_this_interpreter_running_the_app_cli(
        self, home: Path, repo: Path
    ) -> None:
        """Not `fantabot-app`, and not a shell. launchd resolves no PATH and sources no
        profile, so the only thing certain to exist is the interpreter running right now."""
        assert _job(repo).program == [
            sys.executable,
            "-m",
            "fantabot_app.cli",
            "schedule",
            "run",
            "--league",
            "4103937",
            "--arm",
        ]

    def test_an_unarmed_job_omits_arm_entirely(self, home: Path, repo: Path) -> None:
        """Absent, not `--arm=false`: the flag *is* the lock, and a lock spelled as a value
        is one typo from open."""
        assert "--arm" not in _job(repo, arm=False).program

    def test_a_working_directory_with_no_dotenv_is_refused_by_name(
        self, home: Path, tmp_path: Path
    ) -> None:
        """`.env` is where `FANTABOT_AUTO_ACT` lives. A job pointed at a directory without
        one runs hourly and refuses every time, which reads as a broken bot."""
        bare = tmp_path / "elsewhere"
        bare.mkdir()
        with pytest.raises(schedule.ScheduleRefused) as caught:
            schedule.build_job(working_dir=bare, league=4103937)
        assert str(bare) in str(caught.value)
        assert ".env" in str(caught.value)

    def test_the_plist_is_hourly_and_at_load(self, home: Path, repo: Path) -> None:
        """3600 and `RunAtLoad`. At load is what catches a matchday up after a reboot —
        launchd does not replay the interval ticks that passed while the Mac was off."""
        body = plistlib.loads(schedule.render(_job(repo)))
        assert body["StartInterval"] == 3600
        assert body["RunAtLoad"] is True
        assert body["Label"] == "com.fantabot.lineup"
        assert body["WorkingDirectory"] == str(repo)

    def test_the_logs_are_on_the_internal_disk_not_beside_the_repository(
        self, home: Path, repo: Path
    ) -> None:
        """The repository is on an external SSD. If it is unmounted the job cannot start —
        and the only evidence of that is a log launchd writes somewhere still readable."""
        body = plistlib.loads(schedule.render(_job(repo)))
        assert body["StandardErrorPath"].startswith(str(paths.logs()))
        assert not body["StandardErrorPath"].startswith(str(repo))


class TestInstall:
    def test_it_writes_the_plist_and_loads_nothing(
        self, home: Path, repo: Path
    ) -> None:
        """The property this whole command is shaped around. A plist on disk does nothing;
        `launchctl bootstrap` is what puts a bidder on the platform, and it stays the
        operator's keystroke."""
        spy = Spy()
        written = schedule.install(_job(repo), launchctl=spy)

        assert written.path.exists()
        assert written.path == paths.launch_agents() / "com.fantabot.lineup.plist"
        # Not `spy.calls == []` any more: `install` reads launchd, so that assertion would
        # forbid the read along with the load. The rule it was always about is the load —
        # the same reads-stay-legal split `tests/test_layers.py` makes for `apileague`.
        assert schedule.loading_calls(spy.calls) == []

    def test_it_creates_the_launch_agents_directory(self, home: Path, repo: Path) -> None:
        assert not paths.launch_agents().exists()
        schedule.install(_job(repo), launchctl=Spy())
        assert paths.launch_agents().is_dir()

    def test_a_second_install_replaces_the_first(self, home: Path, repo: Path) -> None:
        schedule.install(_job(repo, league=1), launchctl=Spy())
        schedule.install(_job(repo, league=2), launchctl=Spy())
        body = plistlib.loads(schedule.plist_path().read_bytes())
        assert "2" in body["ProgramArguments"]
        assert "1" not in body["ProgramArguments"]

    def test_the_bootstrap_line_names_this_uid_and_the_plist(
        self, home: Path, repo: Path
    ) -> None:
        line = schedule.bootstrap_line(schedule.plist_path(), uid=501)
        assert line == f"launchctl bootstrap gui/501 {schedule.plist_path()}"


class TestStatus:
    def test_not_installed_when_there_is_no_plist(self, home: Path) -> None:
        state = schedule.status(launchctl=Spy(code=113), uid=501)
        assert state.installed is False
        assert state.loaded is False

    def test_installed_and_loaded_are_two_different_facts(
        self, home: Path, repo: Path
    ) -> None:
        """A plist on disk that was never bootstrapped is the state `install` deliberately
        leaves behind, and a status that conflated the two would call it live."""
        schedule.install(_job(repo), launchctl=Spy())
        unloaded = schedule.status(launchctl=Spy(code=113), uid=501)
        assert unloaded.installed is True
        assert unloaded.loaded is False

        loaded = Spy(code=0)
        state = schedule.status(launchctl=loaded, uid=501)
        assert state.loaded is True
        assert loaded.calls == [["launchctl", "print", "gui/501/com.fantabot.lineup"]]

    def test_it_reads_the_league_and_the_arming_back_out_of_the_plist(
        self, home: Path, repo: Path
    ) -> None:
        """Read back from the file, not remembered: the plist is what launchd will run, and
        an operator who edited it by hand should see what they edited."""
        schedule.install(_job(repo, league=3584692, arm=False), launchctl=Spy())
        state = schedule.status(launchctl=Spy(code=113), uid=501)
        assert state.league == 3584692
        assert state.armed is False
        assert state.working_dir == repo

    def test_an_unreadable_working_directory_is_reported(
        self, home: Path, repo: Path
    ) -> None:
        """The external-SSD failure, named. launchd will not start a job whose
        `WorkingDirectory` is gone, and nothing else in the app would say why."""
        schedule.install(_job(repo), launchctl=Spy())
        (repo / ".env").unlink()
        repo.rmdir()
        state = schedule.status(launchctl=Spy(code=113), uid=501)
        assert state.working_dir_readable is False


class TestUninstall:
    def test_it_boots_the_job_out_before_removing_the_plist(
        self, home: Path, repo: Path
    ) -> None:
        """Order matters: `bootout` addresses the job by label, and launchd resolves that
        label through the plist. Remove the file first and the job keeps running with
        nothing left to name it."""
        schedule.install(_job(repo), launchctl=Spy())
        seen: list[bool] = []
        spy = Spy()

        def watching(argv: list[str]) -> int:
            seen.append(schedule.plist_path().exists())
            return spy(argv)

        outcome = schedule.uninstall(launchctl=watching, uid=501)

        assert spy.calls == [["launchctl", "bootout", "gui/501/com.fantabot.lineup"]]
        assert seen == [True], "the plist was removed before launchctl was asked"
        assert not schedule.plist_path().exists()
        assert outcome == "removed"

    def test_a_missing_plist_is_reported_and_is_not_an_error(self, home: Path) -> None:
        assert schedule.uninstall(launchctl=Spy(code=113), uid=501) == "not_installed"


class TestPlatform:
    def test_a_non_darwin_platform_is_refused_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """launchd is macOS's. The command exists everywhere so `--help` is honest, and
        says which platform it is on rather than raising a `FileNotFoundError` on
        `launchctl` three calls later. This is also what keeps `app-ci`'s Windows job green."""
        monkeypatch.setattr(schedule, "_platform", lambda: "win32")
        with pytest.raises(schedule.ScheduleRefused) as caught:
            schedule.require_darwin()
        assert "win32" in str(caught.value)
        assert "launchd" in str(caught.value)


class TestTheTwoRunners:
    """`launchctl` swallows its output and `run_command` must not — opposite on purpose."""

    def test_launchctl_captures_so_its_own_errors_do_not_contradict_ours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`launchctl print` on an unloaded label exits 113 and writes "Could not find
        service" to stderr. That is the *answer* to "is it loaded", not a failure, and
        inherited it lands above the line the app prints saying the same thing calmly."""
        seen: dict[str, object] = {}

        class Done:
            returncode = 113

        def fake(argv: list[str], **kw: object) -> Done:
            seen.update(kw)
            return Done()

        monkeypatch.setattr(schedule.subprocess, "run", fake)
        assert schedule.launchctl(["launchctl", "print", "gui/501/x"]) == 113
        assert seen["capture_output"] is True

    def test_the_submit_inherits_its_output_because_that_output_is_the_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The launchd log is the only record of an unattended run's reasoning. Captured
        and dropped, a failed matchday would leave an exit code and nothing to read."""
        seen: dict[str, object] = {}

        class Done:
            returncode = 0

        def fake(argv: list[str], **kw: object) -> Done:
            seen.update(kw)
            return Done()

        monkeypatch.setattr(schedule.subprocess, "run", fake)
        schedule.run_command(["true"], cwd="/tmp")
        assert "capture_output" not in seen
        assert seen["cwd"] == "/tmp"


class TestTheCommand:
    def test_install_prints_the_bootstrap_line_and_says_it_will_submit(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one thing the operator must not misread: running the printed line means the
        bot submits a real lineup to a real lega.

        `launchctl` is injected now that `install` reads it. Left real, this test asked the
        developer's own launchd about the real label — `home` redirects `~`, but the label
        is global — so it passed on a machine with no job and failed on the operator's,
        which is the definition of a test nobody can trust.
        """
        monkeypatch.setattr(schedule, "launchctl", Spy(code=113))
        result = runner.invoke(
            app,
            ["schedule", "install", "--league", "4103937", "--working-dir", str(repo)],
        )
        assert result.exit_code == 0, result.output
        assert "launchctl bootstrap" in result.output
        assert "submit" in result.output.lower()
        assert schedule.plist_path().exists()

    def test_install_over_a_loaded_job_does_not_print_a_line_that_would_fail(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect, at the surface the operator actually reads.

        Measured 2026-09-20 against the live job — loaded, ARMED, `runs = 11` — and
        `install` printed *"Nothing is scheduled yet"* and a `bootstrap` line that fails
        on an already-loaded label. It sent the operator to arm a bot that was armed.
        """
        monkeypatch.setattr(schedule, "launchctl", Spy(code=0))
        args = ["schedule", "install", "--league", "4103937", "--working-dir", str(repo)]
        runner.invoke(app, args)

        again = runner.invoke(app, args)

        assert again.exit_code == 0, again.output
        assert "already loaded" in again.output
        assert "nothing to apply" in again.output
        assert "Nothing is scheduled yet" not in again.output
        assert "launchctl bootstrap" not in again.output

    def test_install_of_a_changed_plist_over_a_loaded_job_says_to_reload_it(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loaded *and* changed is the state that is actually dangerous: launchd goes on
        running the previous definition, so a plist that reads as applied is a bot doing
        something other than what the screen says. `bootout` comes first — it addresses
        the label, which launchd resolves through the file."""
        monkeypatch.setattr(schedule, "launchctl", Spy(code=0))
        base = ["schedule", "install", "--working-dir", str(repo), "--league"]
        runner.invoke(app, [*base, "4103937"])

        changed = runner.invoke(app, [*base, "3584692"])

        assert "PREVIOUS definition" in changed.output
        assert "launchctl bootout" in changed.output
        assert changed.output.index("bootout") < changed.output.index("bootstrap")

    def test_install_refuses_when_no_league_can_be_resolved(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--league` falls back to `FANTABOT_LEAGUE_ID`, and a plist that carried neither
        would fail hourly. It is refused at install, where someone is watching."""
        monkeypatch.setenv("FANTABOT_LEAGUE_ID", "0")
        result = runner.invoke(
            app, ["schedule", "install", "--working-dir", str(repo)]
        )
        assert result.exit_code == 1
        assert "--league" in result.output
        assert not schedule.plist_path().exists()

    def test_run_starts_postgres_and_then_submits(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Order is the point: the database is brought up *before* the submit, because the
        first run after a reboot is the one that decides a matchday."""
        order: list[str] = []

        class FakeProvisioner:
            def start(self) -> str:
                order.append("db")
                return "postgresql:///fantabot"

        monkeypatch.setattr("fantabot_app.cli._provisioner", FakeProvisioner)
        argv: list[list[str]] = []

        def fake_run(command: list[str], cwd: str | None = None) -> int:
            order.append("submit")
            argv.append(list(command))
            return 0

        monkeypatch.setattr(schedule, "run_command", fake_run)
        result = runner.invoke(app, ["schedule", "run", "--league", "4103937", "--arm"])

        assert result.exit_code == 0, result.output
        assert order == ["db", "submit"]
        assert argv == [
            [
                sys.executable,
                "-m",
                "fantabot",
                "lineup",
                "submit",
                "--league",
                "4103937",
                "--arm",
                "--scheduled",
            ]
        ]

    def test_run_without_arm_submits_nothing(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProvisioner:
            def start(self) -> str:
                return "postgresql:///fantabot"

        monkeypatch.setattr("fantabot_app.cli._provisioner", FakeProvisioner)
        argv: list[list[str]] = []
        monkeypatch.setattr(
            schedule, "run_command", lambda command, cwd=None: (argv.append(command), 0)[1]
        )
        runner.invoke(app, ["schedule", "run", "--league", "4103937"])
        assert "--arm" not in argv[0]
        assert "--scheduled" in argv[0]

    def test_run_exits_with_the_child_status(
        self, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A launchd job's exit code is the only thing launchd itself records. A wrapper
        that swallowed a failing submit would report every matchday as fine."""

        class FakeProvisioner:
            def start(self) -> str:
                return "postgresql:///fantabot"

        monkeypatch.setattr("fantabot_app.cli._provisioner", FakeProvisioner)
        monkeypatch.setattr(schedule, "run_command", lambda command, cwd=None: 3)
        result = runner.invoke(app, ["schedule", "run", "--league", "4103937", "--arm"])
        assert result.exit_code == 3

    def test_status_and_uninstall_speak_when_nothing_is_installed(self, home: Path) -> None:
        monkeypatch_free = runner.invoke(app, ["schedule", "status"])
        assert monkeypatch_free.exit_code == 0
        assert "not installed" in monkeypatch_free.output.lower()
        removed = runner.invoke(app, ["schedule", "uninstall"])
        assert removed.exit_code == 0
        assert "not installed" in removed.output.lower()


def _fake_interpreter(root: Path, name: str) -> Path:
    """A real file standing in for a uv-managed CPython, at its own versioned path."""
    real = root / "uv" / name / "bin" / "python3.11"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    return real


def _venv_symlink(root: Path, target: Path) -> Path:
    """`app/.venv/bin/python3` — the name the plist carries, pointing at the real one.

    This indirection *is* the hazard: the plist names the symlink, macOS TCC attributes the
    Full Disk Access grant to whatever is actually exec'd, and `uv python` moves that.
    """
    link = root / "venv" / "bin" / "python3"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    return link


class TestTheInterpreterGrant:
    """The Full Disk Access grant is on a path nothing in the repository recorded.

    The job hung for seven minutes with both logs at 0 bytes on 2026-09-18, and the cause
    was macOS TCC: a LaunchAgent starts in `gui/<uid>` with no inherited rights, so it could
    not read `/Volumes/External SSD`. `/bin/sh` gets a clean `EPERM`; **CPython hangs
    instead**, which is why there was no traceback and no output — it never reached user
    code. The fix is a GUI grant only the operator can make, and it attributes to the
    **resolved** interpreter, not to the venv symlink the plist names.

    So a `uv python` upgrade moves the granted binary and the job goes back to hanging, with
    nothing to explain why: the only symptom is a run record that stops appearing. These
    tests are the record that was missing.
    """

    def test_install_records_the_resolved_interpreter_and_not_the_symlink(
        self, home: Path, repo: Path, tmp_path: Path
    ) -> None:
        """The recorded path is what TCC sees, which is never the symlink."""
        real = _fake_interpreter(tmp_path, "cpython-3.11.15")
        link = _venv_symlink(tmp_path, real)

        schedule.install(_job(repo, python=str(link)), launchctl=Spy())

        assert schedule.read_interpreter_record() == real.resolve()

    def test_status_is_quiet_while_the_grant_still_points_at_the_same_binary(
        self, home: Path, repo: Path, tmp_path: Path
    ) -> None:
        real = _fake_interpreter(tmp_path, "cpython-3.11.15")
        link = _venv_symlink(tmp_path, real)
        schedule.install(_job(repo, python=str(link)), launchctl=Spy())

        state = schedule.status(launchctl=Spy())

        assert state.interpreter_drifted is False
        assert state.interpreter_resolved == real.resolve()
        assert state.interpreter_recorded == real.resolve()

    def test_status_names_the_drift_when_a_uv_upgrade_moved_the_interpreter(
        self, home: Path, repo: Path, tmp_path: Path
    ) -> None:
        """The whole point. The plist is untouched and still runs; the grant is gone."""
        old = _fake_interpreter(tmp_path, "cpython-3.11.15")
        link = _venv_symlink(tmp_path, old)
        schedule.install(_job(repo, python=str(link)), launchctl=Spy())

        new = _fake_interpreter(tmp_path, "cpython-3.11.16")
        _venv_symlink(tmp_path, new)  # what `uv python upgrade` leaves behind

        state = schedule.status(launchctl=Spy())

        assert state.interpreter_drifted is True
        assert state.interpreter_recorded == old.resolve()
        assert state.interpreter_resolved == new.resolve()

    def test_a_job_installed_before_the_record_existed_reads_as_unrecorded(
        self, home: Path, repo: Path, tmp_path: Path
    ) -> None:
        """Unrecorded and drifted are different facts, and the operator acts on them
        differently: one is re-run `install`, the other is re-grant in System Settings.
        Reporting the first as the second sends them to a dialog that changes nothing."""
        real = _fake_interpreter(tmp_path, "cpython-3.11.15")
        link = _venv_symlink(tmp_path, real)
        schedule.install(_job(repo, python=str(link)), launchctl=Spy())
        schedule.interpreter_record_path().unlink()

        state = schedule.status(launchctl=Spy())

        assert state.interpreter_recorded is None
        assert state.interpreter_drifted is False
        assert state.interpreter_resolved == real.resolve()

    def test_an_interpreter_that_is_gone_is_reported_as_gone(
        self, home: Path, repo: Path, tmp_path: Path
    ) -> None:
        """A dangling symlink resolves to a path without raising, so `resolve()` alone
        cannot tell "moved" from "deleted". launchd fails such a job outright rather than
        hanging, which is a different message and a different fix."""
        real = _fake_interpreter(tmp_path, "cpython-3.11.15")
        link = _venv_symlink(tmp_path, real)
        schedule.install(_job(repo, python=str(link)), launchctl=Spy())
        real.unlink()

        state = schedule.status(launchctl=Spy())

        assert state.interpreter_resolved is None
        assert state.interpreter_drifted is True

    def test_uninstall_takes_the_record_with_the_plist(
        self, home: Path, repo: Path, tmp_path: Path
    ) -> None:
        """A record outliving its job would report drift on a machine with no job at all."""
        link = _venv_symlink(tmp_path, _fake_interpreter(tmp_path, "cpython-3.11.15"))
        schedule.install(_job(repo, python=str(link)), launchctl=Spy())

        schedule.uninstall(launchctl=Spy())

        assert not schedule.interpreter_record_path().exists()

    def test_the_status_command_names_both_paths_and_what_to_do(
        self, home: Path, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both paths, because the operator has to paste one into ⇧⌘G in System Settings,
        and the sentence that says the job will hang rather than fail."""
        old = _fake_interpreter(tmp_path, "cpython-3.11.15")
        link = _venv_symlink(tmp_path, old)
        schedule.install(_job(repo, python=str(link)), launchctl=Spy())
        new = _fake_interpreter(tmp_path, "cpython-3.11.16")
        _venv_symlink(tmp_path, new)
        monkeypatch.setattr(schedule, "launchctl", Spy(code=schedule.NOT_LOADED_CODE))

        result = runner.invoke(app, ["schedule", "status"])

        assert result.exit_code == 0, result.output
        assert str(new.resolve()) in result.output
        assert str(old.resolve()) in result.output
        assert "Full Disk Access" in result.output


class TestInstallReportsWhatLaunchdAlreadyHas:
    """`install` used to print "Nothing is scheduled yet" over a job that had run 11 times.

    Measured against the operator's live job on 2026-09-20: loaded, ARMED, `runs = 11`,
    `last exit code = 0` — and `install` said nothing was scheduled and printed a
    `bootstrap` line that would have failed. That is the command whose output guards a
    bot with real credits, so a sentence it cannot know is a sentence it must not say.

    The fix reads launchd. The property the command is shaped around is unchanged and is
    now stated more precisely: it may **read**, it may never **load**.
    """

    def test_loading_calls_catches_every_verb_that_puts_a_job_into_launchd(self) -> None:
        caught = schedule.loading_calls(
            [
                ["launchctl", "bootstrap", "gui/501", "/x.plist"],
                ["launchctl", "kickstart", "-p", "gui/501/com.fantabot.lineup"],
                ["launchctl", "load", "/x.plist"],
            ]
        )
        assert len(caught) == 3

    def test_a_read_is_not_a_load(self) -> None:
        """`print` asks; it does not arm anything. Forbidding it would forbid the fix."""
        assert schedule.loading_calls([["launchctl", "print", "gui/501/x"]]) == []

    def test_bootout_is_not_a_load(self) -> None:
        """`uninstall` calls it, and removing a job is the opposite of starting one."""
        assert schedule.loading_calls([["launchctl", "bootout", "gui/501/x"]]) == []

    def test_a_path_that_merely_contains_a_verb_is_not_a_load(self) -> None:
        """Whole words. `/Users/me/start/x.plist` is a path, not a `start`."""
        assert schedule.loading_calls([["launchctl", "print", "/Users/me/start/x.plist"]]) == []

    def test_it_reports_a_label_launchd_already_has(self, home: Path, repo: Path) -> None:
        """`launchctl print` exits 0 for a loaded label — the same read `status` makes."""
        written = schedule.install(_job(repo), launchctl=Spy(code=0))
        assert written.loaded is True

    def test_it_reports_a_label_launchd_does_not_have(self, home: Path, repo: Path) -> None:
        """113 and "Could not find service" is the answer, not a failure."""
        written = schedule.install(_job(repo), launchctl=Spy(code=113))
        assert written.loaded is False

    def test_the_read_addresses_this_label_in_the_uid_it_was_given(
        self, home: Path, repo: Path
    ) -> None:
        """A uid that is deliberately **not** this machine's.

        It was 501, which is the developer's own, so `domain_target(job.label)` with the
        `uid` dropped produced the identical string and the assertion could not fail. The
        mutation that drops it is caught only against a uid nobody has.
        """
        spy = Spy()
        schedule.install(_job(repo), launchctl=spy, uid=777)
        assert spy.calls == [["launchctl", "print", "gui/777/com.fantabot.lineup"]]

    def test_the_first_install_counts_as_changed(self, home: Path, repo: Path) -> None:
        assert schedule.install(_job(repo), launchctl=Spy()).changed is True

    def test_re_installing_the_same_job_changes_nothing(self, home: Path, repo: Path) -> None:
        """The operator's actual case: `install` re-run only to record the grant. Nothing
        to bootstrap, and the plist launchd holds is still the right one."""
        schedule.install(_job(repo), launchctl=Spy())

        assert schedule.install(_job(repo), launchctl=Spy()).changed is False

    def test_a_different_lega_is_a_changed_plist(self, home: Path, repo: Path) -> None:
        """Loaded *and* changed is the one state that needs bootout then bootstrap:
        launchd goes on running the previous definition until it is told otherwise."""
        schedule.install(_job(repo, league=1), launchctl=Spy())

        assert schedule.install(_job(repo, league=2), launchctl=Spy()).changed is True
