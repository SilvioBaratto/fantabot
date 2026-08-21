"""T5: the prompt.

Pure — player plus window in, string out. The run date is passed in rather than
read from the clock, so the same inputs always produce the same bytes and a test
can pin the wording without being a time bomb.
"""

from datetime import date

from fantabot.news.pool import PoolPlayer
from fantabot.news.prompt import PREFERRED_DOMAINS, build_prompt

ZACCAGNI = PoolPlayer(
    id="4521",
    nome="Zaccagni",
    squadra="LAZ",
    ruolo="Centrocampista",
    ruoli_mantra="W;T",
)
RUN_DAY = date(2026, 10, 7)


def _prompt(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "player": ZACCAGNI,
        "lookback_days": 14,
        "today": RUN_DAY,
    }
    kwargs.update(overrides)
    return build_prompt(**kwargs)  # type: ignore[arg-type]


def test_the_prompt_is_deterministic() -> None:
    assert _prompt() == _prompt()


def test_the_prompt_identifies_the_player() -> None:
    prompt = _prompt()

    assert "Zaccagni" in prompt
    assert "LAZ" in prompt
    assert "Centrocampista" in prompt


def test_the_prompt_carries_the_frozen_mantra_tag() -> None:
    # The model must be told what tag we hold, so it can say whether he still
    # plays there. It is never asked whether the tag is "stale" — that is ours.
    assert "W;T" in _prompt()


def test_the_prompt_lists_all_twelve_mantra_codes() -> None:
    prompt = _prompt()
    for code in ("Por", "Dc", "B", "Dd", "Ds", "E", "M", "C", "T", "W", "A", "Pc"):
        assert code in prompt


def test_the_prompt_states_the_window_and_the_dates_it_spans() -> None:
    prompt = _prompt()

    assert "14" in prompt
    assert "2026-10-07" in prompt
    assert "2026-09-23" in prompt  # 14 days before the run date


def test_the_window_is_a_parameter() -> None:
    assert "21" in _prompt(lookback_days=21)


def test_the_prompt_names_the_preferred_sources_without_restricting_them() -> None:
    prompt = _prompt()

    for domain in PREFERRED_DOMAINS:
        assert domain in prompt
    assert "non esclusiv" in prompt.lower()


def test_the_preferred_sources_are_the_four_agreed_ones() -> None:
    assert PREFERRED_DOMAINS == (
        "fantacalcio.it",
        "gazzetta.it",
        "tuttomercatoweb.com",
        "il sito ufficiale del club",
    )


def test_the_prompt_states_the_recency_rule() -> None:
    # The mitigation for choosing a 14-day window over 7: without it a fortnight-old
    # story scores the same as this morning's, and the series lags reality.
    prompt = _prompt().lower()

    assert "3 giorni" in prompt
    assert "risolt" in prompt  # a resolved story must stop depressing the score


def test_the_prompt_requires_dates_in_the_summary() -> None:
    assert "data" in _prompt().lower()


def test_the_prompt_restricts_fonti_to_urls_actually_read() -> None:
    assert "davvero" in _prompt().lower()


def test_the_prompt_says_silence_is_a_valid_answer() -> None:
    # confidenza=0 with an empty ruolo_campo is the honest answer, not a fallback.
    prompt = _prompt().lower()

    assert "confidenza" in prompt
    assert "0" in prompt
    assert "nessuna notizia" in prompt


def test_the_prompt_does_not_ask_for_a_role_verdict() -> None:
    # It must ask what he plays as, never whether our tag is wrong.
    assert "ruolo_campo" in _prompt()
