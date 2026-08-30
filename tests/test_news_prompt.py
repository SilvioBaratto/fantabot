"""T5: the prompt.

Pure — player plus window in, string out. The run date is passed in rather than
read from the clock, so the same inputs always produce the same bytes and a test
can pin the wording without being a time bomb.
"""

from datetime import date

from fantabot.domain.news.pool import PoolPlayer
from fantabot.domain.news.prompt import (
    PREFERRED_DOMAINS,
    build_prompt,
    build_system_prompt,
    build_user_prompt,
)

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


def test_the_prompt_lists_the_curated_domains_and_a_club_escape() -> None:
    prompt = _prompt().lower()

    for domain in PREFERRED_DOMAINS:
        assert domain in prompt
    # The search is now steered to a limited high-value set, not the open web...
    assert "principalmente" in prompt
    # ...with the one escape the obscure-player case needs: the club's own site.
    assert "ufficiale" in prompt


def test_the_preferred_sources_are_the_curated_high_value_set() -> None:
    # Deliberately limited to fetchable, high-value fantacalcio sources. gazzetta.it
    # is gone: Anthropic's crawler cannot fetch it, so every visit was a wasted turn.
    assert PREFERRED_DOMAINS == (
        "fantacalcio.it",
        "fantamaster.it",
        "fantacalciopedia.com",
        "tuttomercatoweb.com",
        "sport.sky.it",
    )


def test_the_prompt_drops_the_unfetchable_domain() -> None:
    assert "gazzetta.it" not in _prompt().lower()


def test_the_prompt_caps_searches_and_sources() -> None:
    # Bounding the fetch loop is the dominant cost lever: later turns re-send every
    # page already fetched, so a source ceiling caps the input the run re-bills.
    prompt = _prompt().lower()
    assert "al massimo" in prompt


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


# --- T2: the split into a cached system prompt + a per-player user prompt --------------
# The stable instructions become byte-identical across every player in a run, so they
# ride the prompt cache instead of being re-billed 523 times. Only the player varies.


def test_system_prompt_carries_the_instructions_not_the_player() -> None:
    system = build_system_prompt(14, RUN_DAY)
    # The instructional body is here...
    assert "principalmente" in system.lower()
    assert "nessuna notizia" in system.lower()
    assert "ruolo_campo" in system
    for code in ("Por", "Dc", "B", "Dd", "Ds", "E", "M", "C", "T", "W", "A", "Pc"):
        assert code in system
    # ...but no player identity, or it could not cache across players.
    assert "Zaccagni" not in system
    assert "LAZ" not in system
    assert "W;T" not in system


def test_system_prompt_is_identical_for_any_two_players() -> None:
    # It takes no player, so within a run (same window/date) it is one constant string.
    assert build_system_prompt(14, RUN_DAY) == build_system_prompt(14, RUN_DAY)
    other = PoolPlayer(id="9", nome="Immobile", squadra="BOL", ruolo="Attaccante", ruoli_mantra="Pc")
    assert other.nome not in build_system_prompt(14, RUN_DAY)


def test_user_prompt_carries_only_the_player() -> None:
    user = build_user_prompt(ZACCAGNI)
    assert "Zaccagni" in user
    assert "LAZ" in user
    assert "Centrocampista" in user
    assert "W;T" in user
    # The instructions moved to the system prompt — they must not be duplicated here,
    # or the per-player message would carry the very tokens the split removed.
    assert "principalmente" not in user.lower()
    assert "nessuna notizia" not in user.lower()


def test_build_prompt_still_contains_both_halves() -> None:
    # Back-compat: --print-prompt and the assertions above still see one full prompt.
    combined = _prompt()
    assert "Zaccagni" in combined
    assert "principalmente" in combined.lower()
