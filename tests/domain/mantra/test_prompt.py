from fantabot.domain.mantra.gates import MAX_ROLES_PER_SLOT
from fantabot.domain.mantra.prompt import SCHEMI_PROMPT


def test_schemi_prompt_does_not_cap_a_slot_below_the_gate_ceiling():
    assert "Mai 3" not in SCHEMI_PROMPT
    assert "1 o 2 codici" not in SCHEMI_PROMPT
    assert f"fino a {MAX_ROLES_PER_SLOT} codici" in SCHEMI_PROMPT


def test_schemi_prompt_names_the_three_role_slot():
    assert "T/A/Pc" in SCHEMI_PROMPT or '"T","A","Pc"' in SCHEMI_PROMPT
