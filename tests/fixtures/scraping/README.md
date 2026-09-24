# tests/fixtures/scraping

Three real pages from fantacalcio.it, gzipped, recorded **2026-09-24**. They exist because
`adapters/scraping/` held ~1,030 lines of HTML parsing with no test at all, and the failure
mode the modules themselves name — "page structure may have changed", answered with
`SystemExit(1)` — is the one thing a synthetic fixture cannot catch. A hand-built page tests
the test author's reading of the parser; a recorded one tests the parser against the site.

| file | raw | gz | source |
|---|---|---|---|
| `voti-2024-25-g24.html.gz` | 1,234,833 | 45,563 | `/voti-fantacalcio-serie-a/2024-25/24` |
| `quotazioni-2024-25.html.gz` | 1,532,095 | 54,955 | `/quotazioni-fantacalcio/2024-25` |
| `statistiche-2024-25-italia.html.gz` | 1,997,231 | 61,017 | `/statistiche-serie-a/2024-25/italia` |

Fetched through each module's own `fetch_html`, so the `User-Agent` and the one-second
`REQUEST_DELAY_SECONDS` between requests were the scrapers' own. Compressed with `mtime=0`
so the bytes are reproducible and a re-record shows a real diff rather than a timestamp.

**2024/25 on purpose.** A completed season does not move, so a fixture from it keeps meaning
one thing. A current-season page would drift under the test every week and the test would be
re-recorded until it asserted nothing.

## The ground truth these were checked against

Measured when they were recorded, against the 52,324 rows already in `match_grain` — which
is what makes them fixtures rather than just saved bytes:

- `voti` g24 parses **337 rows**; `match_grain` holds **337** for `2024/25` giornata 24.
- Of those, **317 carry a `player_id`** and every one agrees with the database on voto,
  fantavoto, goals, assists and MVP — **zero mismatches**. The other **20 have no
  `player_id`**, which is a property of the page and not a parse failure.
- `quotazioni` parses **679 rows**; the database holds **679** players for that season.
- `statistiche/italia` parses **679 rows**.

## `FANTAVOTO_GLITCH_THRESHOLD = 30`, verified

`voti.py` explains the threshold with "real fantavoto whole numbers observed in the wild top
out at ~21". Measured across all 52,324 rows:

| column | min | max | rows 22–29 | rows ≥ 30 |
|---|---|---|---|---|
| `fantavoto_fc` | −1.50 | **21.00** | 0 | 0 |
| `fantavoto_stat` | −1.50 | **21.00** | 0 | 0 |
| `fantavoto_italia` | −2.00 | **21.00** | 0 | 0 |
| `voto_fc` (base) | 3.00 | **9.50** | 0 | 0 |

The claim is exact, the threshold has a nine-point margin, and the base-voto half — "never
legitimately reaches two digits" — is confirmed by a maximum of 9.50. The highest recorded
fantavoto is Retegui, 2024/25 g24, voto 9 with four goals and an MVP: **21.00**, and he is
in this fixture.

## Re-recording

Only when a test fails because the site changed, which is the signal these exist to give —
never to make a red test green. Re-record with each module's own `fetch_html` and
`gzip.compress(..., mtime=0)`, then re-run the cross-check above before trusting the new
bytes. A fixture that no longer agrees with `match_grain` means the parser changed
behaviour, not that the fixture is stale.
