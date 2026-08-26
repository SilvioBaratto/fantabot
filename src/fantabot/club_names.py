"""The bridge between the two club vocabularies. Pure: no file, no session.

``quotazioni``/``statistiche``/``qi_bias``/``target_price`` identify a club by a
three-letter code; ``voti``/``bonus_malus`` use the full name. Nothing in the
data states the correspondence, so it is derived — and then **gated**, because a
wrong or partial mapping does not fail loudly. It makes later joins return zero
rows while every table still looks populated.

The rule is that the code is the name's first three letters, upper-cased.

**It reads no file and must not gain one.** Both vocabularies live in Postgres
now: the codes in ``quotazioni.squadra``, the names in ``voti.squadra_raw``. The
functions here take iterables and return a dict, so they can be fed from either
without knowing where the strings came from.
"""

from __future__ import annotations

from collections.abc import Iterable


class TeamMappingError(RuntimeError):
    """The code-to-name mapping is not trustworthy, so nothing is written."""


def code_for(nome_completo: str) -> str:
    """``"Fiorentina"`` -> ``"FIO"``."""
    return nome_completo.strip()[:3].upper()


def build_mapping(nomi: Iterable[str], codici: Iterable[str]) -> dict[str, str]:
    """Map every code to its full name, or raise. Pure, and fail-closed.

    Refuses rather than returning a partial mapping. A NULL ``nome_completo``
    would make later joins silently drop rows, which is a worse failure than an
    import that stops.
    """
    nomi = sorted({n.strip() for n in nomi if n.strip()})
    codici = sorted({c.strip().upper() for c in codici if c.strip()})

    mapping: dict[str, list[str]] = {}
    for nome in nomi:
        mapping.setdefault(code_for(nome), []).append(nome)

    collisions = {code: names for code, names in mapping.items() if len(names) > 1}
    if collisions:
        raise TeamMappingError(
            f"the first three letters are not unique across clubs: {collisions}"
        )

    unresolved = [code for code in codici if code not in mapping]
    if unresolved:
        raise TeamMappingError(
            f"no full name found for {len(unresolved)} code(s): {', '.join(unresolved)}"
        )

    return {code: names[0] for code, names in mapping.items()}
