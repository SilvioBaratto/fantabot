"""Every planning path states the shape it is priced against, and an unrecorded one is refused.

`num_teams`/`num_credits` have been parameters of `read_plan_inputs` since 2026-09-05, with an
8x500 default — our room. **Five of six callers never passed one.** Nothing looked wrong
because the default was right for the only room anyone ran it in, and `docs/fantalab/00 §13`
is explicit about why that is still a bug: *"se un numero o una regola d'asta compare scritto
nel codice, è un bug"*, and the next asta is the riparazione in January or a friend's league.

`asta calibrate` is the case that proves it rather than merely illustrating it. It forwarded
`--teams/--credits` to `recorded_auctions` — the replay corpus — and called `read_plan_inputs`
with no shape at all, so a `--teams 10 --credits 1000` sweep graded a 10x1000 corpus against
prices averaged from 8x500 rooms. The grader and the thing being graded priced differently,
and the output was a table of plausible numbers.

**And an empty corpus now raises.** `mean_prices({})` is legal, an empty `prices` mapping is a
legal argument, and `DEFAULT_PRICE = 1` then applies to everybody — so the budget constraint
goes vacuous and nothing anywhere raises. Measured 2026-09-05: a 25-man Classic rosa bought
for 25 credits of 500, 22 of its 25 slots differing from the corpus-priced plan.
"""

from __future__ import annotations

import ast

import pytest
from _paths import module_file

from fantabot.domain.asta.prices import NoCorpus


class TestTheRefusal:
    def test_it_names_the_shape_that_was_asked_for(self) -> None:
        exc = NoCorpus("10x1000 mantra", ["8x500 mantra (6625 sales)"])

        assert "10x1000 mantra" in str(exc)
        assert exc.shape == "10x1000 mantra"

    def test_it_lists_the_shapes_that_are_recorded(self) -> None:
        """The operator's next move is to pick one. A refusal that does not say what *is*
        available sends them to SQL."""
        exc = NoCorpus("10x1000 mantra", ["8x500 classic (32100 sales)", "8x500 mantra (6625)"])

        assert "8x500 classic (32100 sales)" in str(exc)
        assert exc.recorded == ("8x500 classic (32100 sales)", "8x500 mantra (6625)")

    def test_an_empty_corpus_says_so_rather_than_listing_nothing(self) -> None:
        assert "none at all" in str(NoCorpus("8x500 mantra", []))

    def test_it_says_why_it_is_not_a_fallback(self) -> None:
        """No nearest-shape fallback. Prices are comparable only within one shape — that is
        why the corpus is filtered by it and why no budget normalization is applied."""
        assert "comparable only within a shape" in str(NoCorpus("9x400 mantra", []))

    def test_it_is_a_lookup_error_so_a_bare_except_valueerror_cannot_swallow_it(self) -> None:
        assert issubclass(NoCorpus, LookupError)
        assert not issubclass(NoCorpus, ValueError)


class TestEveryCallSiteStatesAShape:
    """Read from the source, because a call that omits a keyword argument is invisible
    otherwise: it does not fail, it silently prices against 8x500.

    An AST scan rather than a grep — `interface/asta.py` mentions `read_plan_inputs` in four
    comments, and a text scan would count those as call sites.
    """

    @staticmethod
    def _calls_in(dotted: str) -> list[ast.Call]:
        tree = ast.parse(module_file(dotted).read_text(encoding="utf-8"))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "read_plan_inputs")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "read_plan_inputs")
            )
        ]

    @pytest.mark.parametrize(
        "dotted", ["fantabot.interface.asta", "fantabot.application.plan_request"]
    )
    def test_no_call_omits_the_shape(self, dotted: str) -> None:
        calls = self._calls_in(dotted)
        assert calls, f"{dotted} makes no `read_plan_inputs` call — this scan reads nothing"

        missing = [
            call.lineno
            for call in calls
            if not {"num_teams", "num_credits"} <= {kw.arg for kw in call.keywords if kw.arg}
        ]
        assert not missing, (
            f"{dotted} calls read_plan_inputs without a shape at lines {missing}. "
            "An omitted shape does not fail — it silently prices against 8x500."
        )

    def test_the_scan_ignores_the_four_prose_mentions(self) -> None:
        """A grep for `read_plan_inputs` in `interface/asta.py` finds comments too."""
        source = module_file("fantabot.interface.asta").read_text(encoding="utf-8")

        assert source.count("read_plan_inputs") > len(self._calls_in("fantabot.interface.asta"))


class TestTheCommandsExposeIt:
    """`asta live`, `asta calibrate` and `asta bid` each gained `--teams`/`--credits`.

    They have to: none of them can read the room's own shape. `asta bid` is unauthenticated
    and cannot even read its `asta_type` — which is why `--format` exists — and a wrong shape
    prices against somebody else's game as surely as a wrong format does.

    `asta optimize` was the sixth site and the one this class originally missed: it took
    `PlanRequest`'s 8x500 default, which is explicit in the code and unreachable to the
    operator. "All six sites pass an explicit shape" was 5 of 6 until the Checkpoint B audit
    said so.
    """

    @pytest.mark.parametrize(
        "command", ["asta_optimize", "asta_live", "asta_calibrate", "asta_bid"]
    )
    def test_the_shape_is_an_option(self, command: str) -> None:
        import inspect

        from fantabot.interface import asta

        params = inspect.signature(getattr(asta, command)).parameters

        assert "teams" in params, f"{command} cannot be told the corpus shape"
        assert "credits" in params

    def test_calibrate_gives_the_same_shape_to_both_corpora(self) -> None:
        """It gave it to one. The replay corpus was 10x1000 and the prices were 8x500."""
        tree = ast.parse(module_file("fantabot.interface.asta").read_text(encoding="utf-8"))
        (calibrate,) = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "asta_calibrate"
        ]
        shaped = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id  # type: ignore[union-attr]
            for node in ast.walk(calibrate)
            if isinstance(node, ast.Call)
            and {"num_teams", "num_credits"} <= {kw.arg for kw in node.keywords if kw.arg}
        }

        assert {"read_plan_inputs", "recorded_auctions"} <= shaped
