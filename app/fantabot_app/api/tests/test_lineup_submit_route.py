"""`POST /lineup/submit` — a dry run unless the request asks, and even then only if the env does.

This is the app's **first acting route**, and every test here proves it did not act. The
platform is faked throughout: a test that reached one would submit a real lineup.

`teamLineup_submit(` left `app/tests/test_fitness.py`'s acting ban in the commit that built
this route. A substring ban can express "never", not "only behind two locks", and "never"
stopped being true — so what guards this path now is the arming contract itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.lineup import SUBMIT_OUTCOMES


#: A throwaway Fernet key, minted rather than written down — a literal in a tracked file is
#: a key literal whatever it opens, and a plausible-looking one would not construct.
def _a_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


KEY = _a_key()


class _Plan:
    def __init__(self, module: str, *, mday: int = 3, cmday: int = 4) -> None:
        self.module, self.mday, self.cmday = module, mday, cmday
        self.starts, self.bench = [11, 12], [21]


class _Platform:
    """Records every submit. "Did not act" is checkable, not assumed."""

    def __init__(
        self, *, refuse: tuple[str, ...] = (), read_raises: Exception | None = None
    ) -> None:
        self.refuse = refuse
        #: What the confirming read fails with, *after* the POST has returned 200.
        self.read_raises = read_raises
        self.submitted: list[str] = []

    def teamLineup_submit(self, _lid: int, body: dict[str, Any], **_k: Any) -> dict[str, Any]:
        from fantabot.domain.lineup.errors import LineupRejected

        module = str(body.get("mdl", "?"))
        self.submitted.append(module)
        if module in self.refuse:
            raise LineupRejected("LUP009")
        return {"teamLineupDto": {"mdl": module}}

    def teamLineup_read(self, _lid: int, _c: int, **_k: Any) -> dict[str, Any]:
        if self.read_raises is not None:
            raise self.read_raises
        return {"teamLineupDto": {"starts": [1, 2, 3], "ldate": "2026-09-05T10:00:00"}}


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def wire(
        plans: list[_Plan] | None = None,
        *,
        auto_act: bool = True,
        refuse: tuple[str, ...] = (),
        read_raises: Exception | None = None,
        mstr: str = "",
    ) -> _Platform:
        from fantabot import config
        from fantabot.application import lineup_submit
        from fantabot.config import settings
        from fantabot.domain.lineup import payload as payload_module

        api = _Platform(refuse=refuse, read_raises=read_raises)
        monkeypatch.setattr(settings, "fantabot_encryption_key", KEY, raising=False)
        # The ambient lock is no longer the singleton's attribute — `decide_arming` re-reads
        # it, because a value bound at import left this very server armed after the operator
        # edited `.env` to disarm. Setting the attribute here would set something nothing
        # reads, and every armed test below would quietly become a `not_armed` test.
        # An environment variable with nothing recorded as dotenv-injected is the
        # "genuinely exported" branch, which outranks the file.
        monkeypatch.setattr(config, "_DOTENV_INJECTED", {})
        monkeypatch.setenv(config.AUTO_ACT_VAR, "true" if auto_act else "false")
        monkeypatch.setattr(
            lineup_submit,
            "build_plans",
            lambda *_a, **_k: (plans if plans is not None else [_Plan("343")], {11: "Svilar"}, 7),
        )
        monkeypatch.setattr(
            "fantabot.adapters.http.apileague.league_status", lambda *_a, **_k: {"mstr": mstr}
        )
        monkeypatch.setattr(
            "fantabot.adapters.http.apileague.teamLineup_submit", api.teamLineup_submit
        )
        monkeypatch.setattr(
            "fantabot.adapters.http.apileague.teamLineup_read", api.teamLineup_read
        )
        monkeypatch.setattr(payload_module, "build", lambda plan: {"mdl": plan.module})
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session", _fake_session
        )
        return api

    return wire


def _post(**body: Any) -> dict[str, Any]:
    with TestClient(app) as client:
        return client.post("/api/v1/lineup/submit", json={"league_id": 4103937, **body}).json()


class TestArmHasNoDefault:
    """The property a browser makes hard: a page can be reloaded, restored by a session
    manager, or left open overnight, and none of those may carry an arming decision."""

    def test_omitting_arm_is_a_422_not_a_dry_run(self, platform) -> None:  # type: ignore[no-untyped-def]
        platform()

        with TestClient(app) as client:
            response = client.post("/api/v1/lineup/submit", json={"league_id": 4103937})

        assert response.status_code == 422, response.json()

    def test_the_body_model_declares_it_required(self) -> None:
        from fantabot_app.api.v1.endpoints.lineup import SubmitRequest

        assert SubmitRequest.model_fields["arm"].is_required(), (
            "`arm` gained a default — a default either way is a decision the last request "
            "makes for the next one"
        )


class TestTheDryRunActsOnNothing:
    def test_arm_false_submits_nothing_and_still_shows_the_plan(self, platform) -> None:  # type: ignore[no-untyped-def]
        api = platform()

        body = _post(arm=False)

        assert body["outcome"] == "not_armed"
        assert body["submitted"] is False
        assert api.submitted == [], "a dry run reached the platform"
        # The XI travels with it — seeing it is the point of a dry run.
        assert body["module"] == "343"
        assert [p["nome"] for p in body["starters"]] == ["Svilar", "12"]

    def test_it_names_the_lock_that_is_shut(self, platform) -> None:  # type: ignore[no-untyped-def]
        platform(auto_act=True)

        assert "did not ask to arm" in _post(arm=False)["reason"]

    def test_and_names_both_when_both_are(self, platform) -> None:  # type: ignore[no-untyped-def]
        """One message for two causes is how ten minutes go into editing the wrong file."""
        platform(auto_act=False)

        reason = _post(arm=False)["reason"]

        assert "FANTABOT_AUTO_ACT" in reason
        assert "did not ask to arm" in reason

    def test_asking_to_arm_with_the_env_off_still_acts_on_nothing(self, platform) -> None:  # type: ignore[no-untyped-def]
        api = platform(auto_act=False)

        body = _post(arm=True)

        assert body["outcome"] == "not_armed"
        assert api.submitted == []
        assert "FANTABOT_AUTO_ACT" in body["reason"]
        assert "did not ask to arm" not in body["reason"]

    def test_the_app_never_tells_a_browser_to_pass_a_flag(self, platform) -> None:  # type: ignore[no-untyped-def]
        platform(auto_act=False)

        assert "--arm" not in _post(arm=False)["reason"]


class TestTheRefusalsThatOutrankArming:
    def test_no_matchday_refuses_before_the_arm_check(self, platform) -> None:  # type: ignore[no-untyped-def]
        """A dry run that printed a plan the armed run would have refused is a rehearsal of
        the wrong thing — so this fires either way."""
        api = platform([_Plan("343", mday=0, cmday=0)], auto_act=False)

        body = _post(arm=False)

        assert body["outcome"] == "no_matchday"
        assert api.submitted == []

    def test_every_module_refused_is_its_own_screen(self, platform) -> None:  # type: ignore[no-untyped-def]
        api = platform([_Plan("343"), _Plan("352")], refuse=("343", "352"))

        body = _post(arm=True)

        assert body["outcome"] == "all_modules_refused"
        assert body["submitted"] is False
        assert api.submitted == ["343", "352"]
        assert body["rejected"] == ["343 (LUP009)", "352 (LUP009)"]


class TestAnArmedSubmit:
    """The only tests here that let a submit through, and the platform is a fake."""

    def test_it_submits_and_reports_the_read_back(self, platform) -> None:  # type: ignore[no-untyped-def]
        api = platform()

        body = _post(arm=True)

        assert body["outcome"] == "submitted"
        assert body["submitted"] is True
        assert api.submitted == ["343"]
        # The read-back is the evidence: what the platform kept, not what we sent.
        assert body["saved_starters"] == 3
        assert body["saved_at"] == "2026-09-05T10:00:00"

    def test_a_refused_module_is_survived(self, platform) -> None:  # type: ignore[no-untyped-def]
        api = platform([_Plan("343"), _Plan("352")], refuse=("343",))

        body = _post(arm=True)

        assert body["outcome"] == "submitted"
        assert body["module"] == "352"
        assert api.submitted == ["343", "352"]
        assert body["rejected"] == ["343 (LUP009)"]

    def test_past_kickoff_warns_and_submits_anyway(self, platform) -> None:  # type: ignore[no-untyped-def]
        """`mstr` is not confirmed to be the lineup deadline, so the platform stays the
        authority — a guess that blocked would lose a matchday to our own caution."""
        api = platform(mstr="2000-01-01T00:00:00")

        body = _post(arm=True)

        assert body["outcome"] == "submitted"
        assert api.submitted == ["343"]
        assert body["past_deadline"] == "2000-01-01T00:00:00"


def test_the_route_returns_only_what_it_pins() -> None:
    """The same discipline as `api/outcomes.py`: a route that returns an unlisted outcome
    renders a screen the frontend has no branch for.

    **Scanned per function, not per module.** It used to read `lineup.py` whole and subtract
    the *other* route's outcomes by name — which worked while the file held two routes and
    stopped the day it held three: `lineup_current`'s `read` arrived as an unpinned outcome
    of the submit route, which it is not. A module-wide scan cannot tell which route an
    outcome belongs to, so it asks the function.
    """
    import ast
    from pathlib import Path

    from fantabot_app.api.v1 import endpoints

    source = (Path(endpoints.__file__).parent / "lineup.py").read_text(encoding="utf-8")
    [submit] = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "lineup_submit"
    ]
    returned = {
        node.value.value
        for node in ast.walk(submit)
        if isinstance(node, ast.keyword)
        and node.arg == "outcome"
        and isinstance(node.value, ast.Constant)
    } | {
        value.value
        for node in ast.walk(submit)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and key.value == "outcome"
        and isinstance(value, ast.Constant)
    }

    assert returned, "the scan found no outcome at all: it is measuring nothing"
    assert set(SUBMIT_OUTCOMES) >= returned, (
        f"the route returns {sorted(returned)} and pins {sorted(SUBMIT_OUTCOMES)}"
    )


class _FakeSession:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_exc: object) -> None:
        return None


def _fake_session(*_a: object, **_k: object) -> _FakeSession:
    return _FakeSession()


class TestAFailedReadBackIsNotAFailedSubmit:
    """The route's half of the same rule.

    The confirming GET runs after `POST /gaming/v1/teamLineup/{division}` has returned 200.
    Anything it raised propagated out of `submit_lineup` and landed in this route's
    `except (ApiTimeout, ApiUnavailable)` arm, which answers `outcome="unreachable"` with
    `submitted` left at its `False` default — rendered by the page as a red "Not submitted"
    about a lineup that is on the platform.
    """

    def test_a_timeout_on_the_read_back_still_reports_submitted(self, platform) -> None:  # type: ignore[no-untyped-def]
        from fantabot.domain.tokens.errors import ApiTimeout

        fake = platform([_Plan("343")], read_raises=ApiTimeout(10))

        body = _post(arm=True)

        assert body["outcome"] == "submitted"
        assert body["submitted"] is True
        assert body["unconfirmed"], "the page cannot tell the operator to go and check"
        assert fake.submitted == ["343"], "it must not re-POST to get its evidence"

    def test_a_confirmed_submit_carries_no_caveat(self, platform) -> None:  # type: ignore[no-untyped-def]
        """The negative control: every successful submit must not look unconfirmed."""
        platform([_Plan("343")])

        body = _post(arm=True)

        assert body["outcome"] == "submitted"
        assert body["unconfirmed"] == ""
        assert body["saved_starters"] == 3
