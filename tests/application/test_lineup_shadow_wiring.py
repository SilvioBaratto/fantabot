"""T36: which model chose the XI, when the projection runs, and what a failure costs.

The hourly job is the thing being protected. Three rules, and every test here is one of
them:

* **the default path does not move.** With no flag and no setting, `submit_lineup` behaves
  exactly as it did — same POST, same record, no projector called;
* **under `indexcompare` the POST happens first** and the projection after, so a chain that
  hangs or raises cannot delay, change or lose the lineup that was going out anyway;
* **under `projection` the plan comes first**, `scheduled_cutoff` is re-checked immediately
  before the POST because the chain takes minutes, and every way the chain can fail is a
  **fallback** to the XI the matcher already built — except `AssertionError`, which is a bug
  and must not be absorbed.

A surface asked for `projection` with no projector **refuses**. It does not quietly POST an
`indexCompare` XI under a record that says `projection` (AD10), and it refuses before the
arm check so a dry run rehearses the same refusal.

The projector is a fake returning default-path types, which is the whole point of the seam:
nothing here imports the projection branch, and neither does `lineup_submit`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from fantabot.adapters.files.lineup_runs import LineupShadow
from fantabot.application.lineup_submit import (
    INDEXCOMPARE,
    MODEL_NOT_ON_SURFACE,
    PROJECTION,
    Projected,
    chosen_model,
    parse_model,
    run_record,
    submit_lineup,
)
from fantabot.domain.lineup.models import PlannedLineup

NOW = datetime(2026, 9, 23, 10, 0, 0)
BASE = PlannedLineup(
    module="343", starts=tuple(range(11)), bench=(20, 21), competition=311681,
    mday=4, cmday=6, tid=10000003,
)
EVALUATED = replace(BASE, module="352", starts=tuple(range(1, 12)), bench=(0, 21))
SHADOW = LineupShadow(
    model=PROJECTION, module="352", starter_ids=tuple(range(1, 12)), bench_ids=(0, 21),
    starters=tuple(f"p{i}" for i in range(1, 12)), bench=("p0", "p21"),
    e_pts=1.85, p_wdl=(0.55, 0.2, 0.25), e_fp=70.5, sd=6.0,
)


class _Store:
    """A token store nothing reads: every apileague call is faked."""


def _fakes(monkeypatch: pytest.MonkeyPatch, *, mstr: str = "2099-01-01T00:00:00") -> list[Any]:
    from fantabot.adapters.http import apileague
    from fantabot.application import lineup_submit

    monkeypatch.setattr(
        lineup_submit, "build_plans", lambda *_a, **_k: ([BASE], {i: f"p{i}" for i in range(30)}, 311681)
    )
    monkeypatch.setattr(apileague, "league_status", lambda *_a, **_k: {"mstr": mstr, "mday": 6})
    posted: list[Any] = []
    monkeypatch.setattr(
        apileague, "teamLineup_submit", lambda _lid, body, **_k: posted.append(body)
    )
    monkeypatch.setattr(apileague, "teamLineup_read", lambda *_a, **_k: {"teamLineupDto": {}})
    return posted


def _submit(**over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "league_id": 4103937, "arm": True, "now": lambda: NOW, "auto_act": True,
        "model": INDEXCOMPARE,
    }
    kwargs.update(over)
    return submit_lineup(_Store(), **kwargs)  # type: ignore[arg-type]


class TestTheModelSetting:
    @pytest.mark.parametrize("raw", ["projection", "PROJECTION", " Projection "])
    def test_the_projection_is_selected_by_name(self, raw: str) -> None:
        assert parse_model(raw) == PROJECTION

    @pytest.mark.parametrize("raw", [None, "", "  ", "indexcompare", "projektion", "1", "true"])
    def test_anything_else_is_the_default_path(self, raw: str | None) -> None:
        """Fails closed (AD4): `Settings()` runs at import, so a typo in `.env` must not
        stop the hourly job at the import line — it must field a lineup the old way."""
        assert parse_model(raw) == INDEXCOMPARE

    def test_it_is_read_now_and_not_at_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`settings` is the module singleton built at first import, so an app server would
        answer every request with the state of the world at boot."""
        from fantabot.config import LINEUP_MODEL_VAR

        monkeypatch.setenv(LINEUP_MODEL_VAR, "projection")
        assert chosen_model() == PROJECTION

        monkeypatch.setenv(LINEUP_MODEL_VAR, "indexcompare")
        assert chosen_model() == INDEXCOMPARE


class TestTheDefaultPathDoesNotMove:
    def test_with_no_projector_nothing_is_computed_and_the_post_is_the_same(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posted = _fakes(monkeypatch)

        outcome = _submit()

        assert len(posted) == 1
        assert outcome.submitted == BASE
        assert (outcome.model, outcome.fallback, outcome.shadow) == (INDEXCOMPARE, "", None)
        assert outcome.warnings == ()

    def test_the_record_says_indexcompare_and_carries_no_shadow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fakes(monkeypatch)

        record = run_record(_submit(), league_id=4103937, scheduled=True, at="2026-09-23T10:00:00")

        assert (record.model, record.fallback, record.shadow) == (INDEXCOMPARE, "", None)
        assert record.warnings == ()


class TestTheShadowUnderIndexcompare:
    def test_the_post_happens_before_the_projection_is_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole reason `--shadow` is free to leave on: the lineup is already on the
        platform before the chain is touched, so it cannot delay, change or lose it."""
        posted = _fakes(monkeypatch)
        order: list[str] = []
        monkeypatch.setattr(
            __import__("fantabot.adapters.http.apileague", fromlist=["x"]),
            "teamLineup_submit",
            lambda _lid, body, **_k: (order.append("post"), posted.append(body))[0],
        )

        def projector(_comp: int) -> Projected:
            order.append("projection")
            return Projected(shadow=SHADOW)

        _submit(projector=projector)

        assert order == ["post", "projection"]

    def test_the_shadow_reaches_the_record_with_ids(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Grading reads ids, never names (A19(7)): a shadow report that matched on names
        would grade the wrong player the first time two share one."""
        _fakes(monkeypatch)

        outcome = _submit(projector=lambda _comp: Projected(shadow=SHADOW))
        record = run_record(outcome, league_id=4103937, scheduled=True, at="x")

        assert record.model == INDEXCOMPARE
        assert record.shadow is not None
        assert record.shadow.model == PROJECTION
        assert record.shadow.starter_ids == tuple(range(1, 12))

    def test_the_post_is_unchanged_by_the_shadow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted = _fakes(monkeypatch)

        outcome = _submit(projector=lambda _comp: Projected(plans=(EVALUATED,), shadow=SHADOW))

        assert len(posted) == 1
        assert outcome.submitted == BASE, "the shadow changed the lineup that was sent"

    @pytest.mark.parametrize(
        "boom", [RuntimeError("x"), KeyError("x"), ValueError("x"), ImportError("numpy")]
    )
    def test_a_projector_that_raises_still_posted_and_is_named(
        self, monkeypatch: pytest.MonkeyPatch, boom: Exception
    ) -> None:
        """Contained (A19(3)), and the **type name only** — a message can carry the DSN and
        this line ends up in a file the app renders."""
        posted = _fakes(monkeypatch)

        def projector(_comp: int) -> Projected:
            raise boom

        outcome = _submit(projector=projector)

        assert len(posted) == 1
        assert outcome.submitted == BASE
        assert outcome.warnings == (f"shadow failed: {type(boom).__name__}",)
        assert str(boom) not in " ".join(outcome.warnings)

    def test_an_assertion_error_is_not_absorbed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bug is a bug. Absorbed, the shadow becomes a thing that never worked and never
        reported — which is the failure the record exists to prevent."""
        _fakes(monkeypatch)

        def projector(_comp: int) -> Projected:
            raise AssertionError("a real bug")

        with pytest.raises(AssertionError, match="a real bug"):
            _submit(projector=projector)

    def test_a_chain_with_no_plan_is_a_named_warning_and_not_a_shadow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fakes(monkeypatch)

        outcome = _submit(
            projector=lambda _comp: Projected(fallback="OpponentUnavailable: too few rounds")
        )

        assert outcome.shadow is None
        assert any("no shadow" in w for w in outcome.warnings)


class TestTheProjectionAsTheModel:
    def test_the_evaluated_plan_is_the_one_posted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted = _fakes(monkeypatch)

        outcome = _submit(
            model=PROJECTION,
            projector=lambda _comp: Projected(plans=(EVALUATED,), shadow=SHADOW),
        )

        assert len(posted) == 1
        assert outcome.submitted == EVALUATED
        assert outcome.model == PROJECTION
        assert outcome.fallback == ""

    def test_a_surface_with_no_projector_refuses_rather_than_posting_indexcompare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AD10. A job told to plan with the projection and quietly POSTing `indexCompare`
        is a job whose record says one thing and whose lineup says another."""
        posted = _fakes(monkeypatch)

        outcome = _submit(model=PROJECTION, projector=None)

        assert posted == []
        assert outcome.refused == MODEL_NOT_ON_SURFACE

    def test_it_refuses_before_the_arm_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A dry run that rehearsed the `indexCompare` XI while the setting said
        `projection` would rehearse the wrong thing."""
        _fakes(monkeypatch)

        outcome = _submit(model=PROJECTION, projector=None, arm=False, auto_act=False)

        assert outcome.refused == MODEL_NOT_ON_SURFACE

    def test_the_refusal_is_a_failure_and_says_why(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fakes(monkeypatch)

        record = run_record(
            _submit(model=PROJECTION, projector=None),
            league_id=4103937, scheduled=True, at="x",
        )

        assert record.status == "failed"
        assert record.code == MODEL_NOT_ON_SURFACE
        assert PROJECTION in record.detail

    @pytest.mark.parametrize("boom", [RuntimeError("x"), ValueError("x"), ImportError("scipy")])
    def test_a_chain_that_raises_falls_back_to_the_matchers_xi(
        self, monkeypatch: pytest.MonkeyPatch, boom: Exception
    ) -> None:
        """The lineup that would have gone out anyway still goes out. A projection submit
        that raised would lose a matchday to a model that is meant to improve one."""
        posted = _fakes(monkeypatch)

        def projector(_comp: int) -> Projected:
            raise boom

        outcome = _submit(model=PROJECTION, projector=projector)

        assert len(posted) == 1
        assert outcome.submitted == BASE
        assert (outcome.model, outcome.fallback) == (INDEXCOMPARE, type(boom).__name__)
        assert any("fell back" in w for w in outcome.warnings)

    def test_a_chain_with_no_plan_falls_back_and_keeps_its_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posted = _fakes(monkeypatch)

        outcome = _submit(
            model=PROJECTION,
            projector=lambda _comp: Projected(fallback="classic: the chain is Mantra-only"),
        )

        assert len(posted) == 1
        assert outcome.submitted == BASE
        assert outcome.model == INDEXCOMPARE
        assert "Mantra-only" in outcome.fallback

    def test_the_warnings_travel_with_the_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fakes(monkeypatch)

        outcome = _submit(
            model=PROJECTION,
            projector=lambda _comp: Projected(
                plans=(EVALUATED,), shadow=SHADOW, warnings=("stale: g5 not calculated",)
            ),
        )

        assert "stale: g5 not calculated" in outcome.warnings

    def test_the_cutoff_is_rechecked_after_the_chain_and_before_the_post(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The chain takes minutes and a matchday can open inside them. Checked once before
        it would rehearse a lineup the armed run then refuses; checked only after, a run
        that was on time when it started could submit into a match already in play."""
        posted = _fakes(monkeypatch, mstr="2026-09-23T12:00:00")
        # On time when the run started, late by the time the chain came back. A clock that
        # did not move could not tell the two checks apart, and the test would pass on the
        # first one alone.
        ticks = iter([
            datetime(2026, 9, 23, 10, 0, 0),   # the first cutoff check: still on time
            datetime(2026, 9, 23, 16, 0, 0),   # after the chain: kickoff has passed
            datetime(2026, 9, 23, 16, 0, 0),
        ])

        outcome = _submit(
            model=PROJECTION,
            projector=lambda _comp: Projected(plans=(EVALUATED,), shadow=SHADOW),
            scheduled=True,
            now=lambda: next(ticks),
        )

        assert posted == [], "submitted into a matchday that opened while the chain ran"
        assert outcome.refused == "matchday-started"

    def test_the_first_cutoff_check_still_refuses_before_the_chain_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control. Late at the first check and the chain is never asked for — minutes
        of work a refused run has no use for."""
        _fakes(monkeypatch, mstr="2026-09-23T00:00:00")
        asked: list[int] = []

        outcome = _submit(
            model=PROJECTION,
            projector=lambda comp: (asked.append(comp), Projected(plans=(EVALUATED,)))[1],
            scheduled=True,
        )

        assert asked == []
        assert outcome.refused == "matchday-started"
