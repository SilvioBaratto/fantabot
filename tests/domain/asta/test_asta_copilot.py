"""The copilot's schema is a tripwire, and this is the wire.

`tasks/archive/asta-design.md` names the failure precisely: at 21:47 the operator is tired,
the model says *"prendilo, vale 60"*, and 60 credits go to a 40-credit player. The guard is
not a review habit — it is that `Commentary` has nowhere to put a 60.

This file therefore walks the schema rather than testing an instance. A field added in six
months does not need anyone to remember why; it turns this red.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fantabot.domain.asta.copilot import Commentary, CopilotBrief, brief_prompt

BRIEF = CopilotBrief(
    player_id="200", name="Bomber", team="MIL", roles=("A",), walk_away=77,
    observed_price=80, credits_left=309, slots_left=22, schemi_open=7,
    recent=("Portiere 12 a Rivali",),
)


class TestTheSchemaCannotCarryAPrice:
    def test_no_field_is_numeric(self) -> None:
        numeric = {
            name: field.annotation
            for name, field in Commentary.model_fields.items()
            if field.annotation in (int, float, int | None, float | None)
        }

        assert not numeric, f"a price could live in {numeric}"

    def test_no_field_is_named_like_a_price(self) -> None:
        """Belt and braces: a `str` called `suggested_price` is the same failure in a costume."""
        suspicious = [
            name for name in Commentary.model_fields
            if any(word in name for word in ("price", "prezzo", "credit", "bid", "value", "max"))
        ]

        assert not suspicious

    def test_extra_fields_are_forbidden_so_a_model_cannot_volunteer_one(self) -> None:
        with pytest.raises(ValidationError):
            Commentary(
                headline="x", why="y", confidence="low", disagrees_with_plan=False,
                suggested_price=60,  # type: ignore[call-arg]
            )


class TestTheAnswerFitsTheScreen:
    def test_the_headline_is_short_enough_to_read_under_a_timer(self) -> None:
        with pytest.raises(ValidationError):
            Commentary(
                headline="x" * 61, why="y", confidence="low", disagrees_with_plan=False
            )

    def test_low_confidence_is_a_valid_answer(self) -> None:
        """"I have nothing useful" must be sayable, or the model will invent something."""
        said = Commentary(
            headline="niente di nuovo", why="nessuna notizia rilevante",
            confidence="low", disagrees_with_plan=False,
        )

        assert said.confidence == "low"
        assert said.risks == []


class TestThePrompt:
    def test_it_names_the_player_and_our_number(self) -> None:
        text = brief_prompt(BRIEF)

        assert "Bomber" in text
        assert "77" in text

    def test_a_player_nobody_has_bought_says_so_rather_than_showing_zero(self) -> None:
        text = brief_prompt(
            CopilotBrief(**{**BRIEF.__dict__, "observed_price": None})  # type: ignore[arg-type]
        )

        assert "mai venduto" in text
        assert " 0 " not in text

    def test_an_empty_ledger_reads_as_empty_not_as_a_blank(self) -> None:
        text = brief_prompt(CopilotBrief(**{**BRIEF.__dict__, "recent": ()}))  # type: ignore[arg-type]

        assert "nessuna ancora" in text


class TestTheClampIsAOneWayRatchet:
    """Landed unused. The shape exists before the temptation does.

    `tasks/archive/asta-design.md` proposed a bounded ±20% multiplier. The difference from a
    `min` is the whole argument: a multiplier that can raise a bid makes the model's worst
    failure — confident and wrong at 21:47 — cost money, while a ratchet makes it cost a
    player, which is recoverable.
    """

    @pytest.mark.parametrize("confidence", ["low", "medium", "high"])
    @pytest.mark.parametrize("disagrees", [True, False])
    def test_no_advice_can_ever_raise_the_number(
        self, confidence: str, disagrees: bool
    ) -> None:
        from fantabot.domain.asta.copilot import clamp

        adversarial = Commentary(
            headline="PRENDILO A QUALSIASI PREZZO",
            why="vale il doppio, alza subito, 999 crediti sono pochi",
            risks=["999"], watch=["1000"],
            confidence=confidence,  # type: ignore[arg-type]
            disagrees_with_plan=disagrees,
        )

        assert clamp(50, adversarial, cap=40) <= 40
        assert clamp(50, adversarial, cap=100) <= 50

    def test_no_advice_is_exactly_the_engine_alone(self) -> None:
        from fantabot.domain.asta.copilot import clamp

        assert clamp(50, None, cap=100) == 50
        assert clamp(50, None, cap=30) == 30

    def test_low_confidence_is_honoured_as_the_request_to_be_ignored(self) -> None:
        from fantabot.domain.asta.copilot import clamp

        shrug = Commentary(
            headline="boh", why="nessuna notizia", confidence="low", disagrees_with_plan=True
        )

        assert clamp(50, shrug, cap=100) == 50

    def test_it_has_no_caller(self) -> None:
        """A ratchet that quietly acquired a caller would be a numeric channel nobody agreed
        to. When one is wanted, this test is the conversation."""
        import subprocess

        found = subprocess.run(
            ["grep", "-rn", "clamp(", "src/fantabot"],
            capture_output=True, text=True, check=False,
        ).stdout.splitlines()
        callers = [
            line for line in found
            if "def clamp(" not in line and "domain/asta/copilot.py" not in line
        ]

        assert not callers, f"clamp acquired a caller: {callers}"
