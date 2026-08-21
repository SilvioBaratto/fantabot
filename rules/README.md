# Fantacalcio.it Official Rules — English Reference

Paraphrased, structured English summaries of fantacalcio.it's official regulations,
built for `strategy.py` / `data_sources/` implementation reference. These are
**summaries in our own words**, not literal translations of the source pages —
each doc keeps the functional facts (point values, formulas, constraints,
procedures) that the bot's logic depends on.

Source: https://www.fantacalcio.it/regolamenti/ (fetched 2026-08-19).

| File | Source page | Covers |
|------|-------------|--------|
| [fantacalcio.md](fantacalcio.md) | [/regolamenti/fantacalcio](https://www.fantacalcio.it/regolamenti/fantacalcio) | Vote sources, S.V. edge cases, card penalties, postponed-match handling |
| [leghe-private.md](leghe-private.md) | [/regolamenti/leghe-private](https://www.fantacalcio.it/regolamenti/leghe-private) | Private league setup: roster size, budget, transfer markets, bench/substitution modes, scoring config, competition formats |
| [sistema-mantra.md](sistema-mantra.md) | [/regolamenti/sistema-mantra](https://www.fantacalcio.it/regolamenti/sistema-mantra) | Mantra role codes, formation schemas, substitution modes (BASIC/EASY/MASTER) |
| [classic-plus.md](classic-plus.md) | [/regolamenti/classic-plus](https://www.fantacalcio.it/regolamenti/classic-plus) | Classic Plus variant (FantaChampions, 2025/26+) |
| [gol-autogol.md](gol-autogol.md) | [/regolamenti/gol-autogol](https://www.fantacalcio.it/regolamenti/gol-autogol) | Goal/own-goal attribution rules for disputed cases |
| [assist.md](assist.md) | [/regolamenti/assist](https://www.fantacalcio.it/regolamenti/assist) | Assist qualification criteria and soft/standard/gold point values |
| [algoritmo-quotazioni.md](algoritmo-quotazioni.md) | [/regolamenti/algoritmo-quotazioni](https://www.fantacalcio.it/regolamenti/algoritmo-quotazioni) | How QI/QA/QAA/FVM market valuations are computed and updated |

## Note on `fantacalcio.md`

The `/regolamenti/fantacalcio` source page does **not** contain the foundational
mechanics you'd expect from the URL (role letters, valid formations, squad
composition, captain rules, auction basics) — it's scoped to vote-assignment
edge cases (S.V., cards, postponements, VAR). Those foundational rules live
implicitly across the other pages here (mainly `leghe-private.md` and
`sistema-mantra.md`) plus `src/fantabot/models.py`'s `VALID_FORMATIONS`.
