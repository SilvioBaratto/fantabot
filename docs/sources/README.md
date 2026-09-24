# docs/sources

Primary documents this repo transcribes, kept because the URLs are season-stamped
and the transcription cannot be re-checked once the publisher moves on.

## `Tabella-sostituzioni-per-schema-2024-2025.pdf`

The per-formation Mantra compatibility table, and the source of
`src/fantabot/data/mantra_compat.json` (package data since the move out of
`data/`; reached through `src/fantabot/domain/shared/resources.py`).
`rules/sistema-mantra.md` gives the general rules in prose and says outright that
this table "is a separate download that isn't captured here" — so this is the only
place the full grid exists.

Retrieved 2026-08-28 from
`https://content.fantacalcio.it/web/risorse/Tabella-sostituzioni-per-schema-2024-2025.pdf`
(HTTP 200, 778,789 bytes). The `2025-2026` and `2026-2027` URLs both return 403,
so this remains the current edition.

Transcribed with a MinerU container rather than by hand or by an agent. **What
follows is a record of the call that was made, not a recipe that runs.**
`http://localhost:3010` was a container on the operator's machine on 2026-08-28.
Nothing in this repository starts it: MinerU is named nowhere else in the tree,
there is no image tag, and the compose scaffold the app once had is gone. Redoing
the transcription means standing MinerU up first and pointing the call at wherever
it lands. The call is kept because its *arguments* are the finding — the three
notes below are about them, and they cost a day to learn.

```bash
# 2026-08-28, against a local MinerU on port 3010. Not reproducible as written.
curl -sS -X POST http://localhost:3010/tasks \
  -F "files=@Tabella-sostituzioni-per-schema-2024-2025.pdf" \
  -F "lang_list=ch" -F "backend=pipeline" -F "table_enable=true" \
  -F "image_analysis=false" -F "formula_enable=false"
```

Three things that matter if it is ever redone:

* **`backend=pipeline`, not the default `hybrid-engine`.** The VLM path spent
  twenty minutes without finishing a single page, all of it captioning the twelve
  pitch diagrams; the classic OCR pipeline finished in two and a half minutes.
  `image_analysis=false` is why.
* **One table per request.** Given a whole page MinerU merges the three upper
  tables into one and drops the fourth entirely, and the merge corrupts cells
  into `'ok ok'` and `'no no'`. Each schema was cropped out with `gs -g<W>x<H>
  -c "<</Install {0 -<offset> translate}>> setpagedevice"` and converted alone.
* **`lang_list=it` is rejected.** `ch` is correct here; the content is role codes.

The legend is wrong in the PDF itself: it says "in colonna i ruoli previsti dallo
schema, nella riga i potenziali sostituti", and the layout is the opposite. Rows
are the schema's slots, columns the twelve role codes.

## `leghe-chunk-Dc2l8Fqx.js`

A chunk of leghe.fantacalcio.it's Angular bundle. It holds the per-module
positional slot order (`S.schemes.mantra`), which is what `starts[i]` is judged
against, and the platform's own malus matrix (`he`).

- Retrieved 2026-09-21 as an unauthenticated static GET from
  `https://leghe.fantacalcio.it/resources/chunk-Dc2l8Fqx.js`.
- 19,672 bytes; sha256
  `421ca5952970224ead95c4953786b83241941e9d64d09ff3bcbf021941698d71`.
- Kept because a content-hashed chunk name does not survive the next deploy.

The findings and the provenance table are in `docs/lineup-slot-order.md`.
