# Fantacalcio.it Official Rules — English Reference

Paraphrased, structured English summaries of fantacalcio.it's official regulations.
These are **summaries in our own words**, not literal translations of the source
pages — each doc keeps the functional facts (point values, formulas, constraints,
procedures) that the bot's logic depends on.

**Who reads them.** They were written for `strategy.py` and `data_sources/`, both
deleted in W2. The consumers today are the pure decision modules, which cite these files
by name — chiefly `src/fantabot/domain/mantra/`, `src/fantabot/domain/lineup/`
(`build.py`, `substitution.py`, `bench_mc.py`) and `src/fantabot/domain/news/mantra.py`
against `sistema-mantra.md`, and `src/fantabot/domain/asta/state.py` against
`leghe-private.md`. Measured 2026-09-24: 34 citations across `src/` and `tests/`, and
every one is to those two files. The other five here are background a reader needs and
no module quotes.

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
`sistema-mantra.md`), and in code as two separate models: the 11 Mantra schemi in
`src/fantabot/data/mantra_schemi.json` (package data, read through
`src/fantabot/domain/shared/resources.py`), and the seven Classic modules in
`src/fantabot/domain/classic/formations.py`, both read from the platform rather
than transcribed from a rules page. `src/fantabot/models.py`'s
`VALID_FORMATIONS`, which this paragraph used to name, was deleted in W2.
