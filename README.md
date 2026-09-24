# fantabot

100% autonomous fantacalcio manager for `leghe.fantacalcio.it`: weekly lineup
submission, asta iniziale (initial auction), asta di riparazione (repair
auction) — all handled without a human clicking anything. None of it goes near the
site's DOM: the lineup runs on `apileague.fantacalcio.it`'s JSON endpoints and the
asta on FantaLab's RTDB. A per-matchday stats source is the one input still unchosen.

## Install & run

The easiest way to run the project is the local web app in [`app/`](app/) — one
command, no Docker. The only tools you install by hand are **git** and
[`uv`](https://docs.astral.sh/uv/); `uv` brings its own Python, Postgres, and compiled
frontend. Nothing is published, so you install from a clone of this repo:

```bash
git clone https://github.com/SilvioBaratto/fantabot.git
cd fantabot

uv tool install ./app     # installs the `fantabot-app` command (fantabot resolved as a path dep)
fantabot-app setup        # provisions its own Postgres (bundled PG18), migrates, installs chromium
fantabot-app              # serves the UI at http://127.0.0.1:8000 and opens your browser
```

That's the whole install. `fantabot-app setup` is safe to re-run; `fantabot-app` is the
everyday launch; `fantabot-app doctor` diagnoses a broken setup.

> **Fresh clone?** The compiled UI (`fantabot_app/web`) is a git-ignored build artifact.
> Released builds bundle it; from a raw checkout, build it once first (needs Node + npm):
> `python app/scripts/build_frontend.py`. Until you do, the app serves a placeholder page.

Full app documentation — everyday commands, where data lives, developer setup — is in
[`app/README.md`](app/README.md). The rest of this file documents the underlying
`fantabot` **CLI** (the engine the app drives) and its developer setup.

## News sentiment (`fantabot news fetch`)

One Claude Agent SDK query per player over `WebSearch` + `WebFetch`, validated
against a pydantic schema, written weekly to `player_sentiment` in Postgres
as a per-player time-series. Runs on the Claude Code OAuth subscription — no
`ANTHROPIC_API_KEY` anywhere.

```bash
fantabot news fetch --limit 5      # smoke test: queries, writes nothing
fantabot news fetch --write        # the weekly run, the whole quotati pool
fantabot news fetch --write --force --lookback-days 21
```

Each row carries an overall `sentiment` plus `disponibilita`, `titolarita`,
`mercato`, `forma`, `rigorista`, `piazzati` and a `confidenza`, with an Italian
`riassunto` and the URLs actually read. `confidenza = 0` means *no coverage was
found* — not a neutral player — and readers must exclude those rows from averages.

It also collects the one Mantra statistic no listone can hold. fantacalcio.it assigns
Mantra roles in late July and never revisits them, so the Mantra `quotazioni` rows drift
from reality by design; `ruolo_campo` records what a player is *actually* being played
as, and `deriva_ruolo` flags when the frozen tag has gone stale.

Suggested cron (Wednesday mornings, in-season):

```cron
0 9 * * 3 cd /path/to/fantabot && /path/to/conda-env/bin/fantabot news fetch --write >> data/news_cron.log 2>&1
```

## Mantra tactical grid (`fantabot mantra-grid`)

One-off, **not** on cron. Collects the 11 Mantra schemas and the per-formation
out-of-position matrix into `src/fantabot/data/mantra_schemi.json` and
`mantra_compat.json`, behind six fail-closed gates. They ship as package data because
they are the legality matcher's input, not runtime state — `domain/asta/legality.py`
reads them through `importlib.resources`, so `asta legality` works from any directory.

```bash
fantabot mantra-grid          # collect and gate, write nothing
fantabot mantra-grid --write  # write only if every gate passes
```

## Status

The asta path is live-capable: `asta optimize` plans, `asta live` advises off a
real room, and `asta bid` places bids over FantaLab's unauthenticated RTDB —
gated behind `FANTABOT_AUTO_ACT`, which is `false` by default.

**Weekly lineup submission is built** — Mantra 2026-09-02, Classic 2026-09-04, both
live-verified, and it never went near the DOM. `fantabot lineup show | plan | submit`,
plus `refresh`, `backtest` and `shadow-report`; the submit is `POST
/gaming/v1/teamLineup/{division}`, behind `FANTABOT_AUTO_ACT` **and** `--arm`, and a dry
run by default. The W2 Classic scaffolding that stood in for it — `lineup.py`,
`auction.py`, `strategy.py` and their DOM stubs — was removed rather than left raising
`NotImplementedError`, and the Classic engine was **rebuilt** 2026-09-03/04 on the JSON
endpoints documented in `docs/leghe-api.md`: `src/fantabot/domain/classic/` holds the
P/D/C/A role model, the seven modules the platform declares, and the four-role band.

Persistence is done: the scraped reference tables, the sentiment series and the whole
lega snapshot live in Postgres — nineteen tables plus `alembic_version`, measured
2026-09-24; `fantabot db check` counts and sizes them. The live room's own record is not a table:
it is the append-only `data/room_journal.jsonl` that `adapters/files/room_journal.py`
writes, which is what survives a mid-asta restart.

## Setup

```bash
conda activate fanta
pip install -e ".[dev]"   # re-run this whenever pyproject.toml changes
playwright install chromium
cp .env.example .env   # fill in LEGA_EMAIL / LEGA_PASSWORD / LEGA_URL / FANTABOT_LEAGUE_ID

# generate an encryption key and paste it into .env as FANTABOT_ENCRYPTION_KEY.
# It encrypts the bearer token at rest; without it `fantabot auth login` refuses to
# open a browser. Never commit it, and never pass it on the command line.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

fantabot-app db start   # the bundled Postgres, at ~/.fantabot/pgdata
alembic upgrade head

# Fill an empty database from the site. This is the only way that works on a
# fresh clone: data/'s contents are git-ignored (.gitignore:12, `data/*`), so the
# CSVs this was seeded from were never part of a checkout, and they are gone.
# Measured end to end on 2026-08-26 — under 5 minutes for four seasons, most of it
# the voti leg.
fantabot db scrape quotazioni    # ~1 GET/season  -> players, teams, quotazioni
fantabot db scrape statistiche   # ~3 GETs/season -> statistiche
fantabot db scrape voti          # ~38 GETs/season, 1s apart -> match_grain
                                 # (the old `voti` + `bonus_malus` pair, merged)
fantabot db price --system classic   # NOTE: one system per run,
fantabot db price --system mantra    # --system defaults to classic
fantabot db dump                 # restore point, outside the repo

fantabot db check         # health, per-table row counts and sizes

fantabot auth login          # interactive, opens a real browser — log in once
fantabot auth status   # what is stored, when it expires, whether it still works
fantabot config-check   # sanity check env is loaded (secrets masked)
pytest                  # decision logic; opens zero sockets
pytest -m db            # integration tier, needs the stack above
```

## Storage

**The `apileague` bearer token lives in Postgres, encrypted.** `fantabot auth login`
opens a real browser, you sign in yourself, and it reads each lega's token out
of `localStorage`, encrypts it with `FANTABOT_ENCRYPTION_KEY` and writes it to
`league_tokens` keyed by lega. Nothing reads a token from disk.

`data/storage_state.json` is **opt-in** now (`fantabot auth login --save-session`).
It holds Playwright's cookies, and as of 2026-08-26 no working code path reads
them — so the default run does not create it.

What the key protects: a database dump, a shared Postgres, Adminer's web UI. It
sits in `.env` beside the database password, so it does not protect against
someone who can read `.env`. That is the honest boundary, and it is still
strictly better than a plaintext token in a file that gets rsynced and backed up.

Postgres is the source of truth. The CSVs in `data/` were the one-time seed it was built
from; they are gone from disk and nothing reads them — the scrapers, the analysis and
`news fetch` all go through the database. The **directory** is still live, though:
`fantabot_data_dir` defaults to `./data`, the live room writes `data/room_journal.jsonl`
there, and `$FANTABOT_HARVEST_DIR` may point the harvest home at `data/aste_live`. See
[`data/README.md`](data/README.md) for the table dictionary, and
`docs/archive/postgres-persistence-spec.md` for why
each departure from the file layout was made.

```bash
fantabot-app db start             # the bundled Postgres; it outlives this command
alembic upgrade head              # apply migrations
alembic check                     # do models and migrations still agree?
fantabot-app db stop              # the only thing that stops it
```

## Commands

One CLI, 35 commands in seven groups plus two one-offs. `fantabot --help` is the whole
surface; `tests/interface/test_cli_command_set.py` pins it. A selection:

```bash
fantabot asta optimize --lam 0.3 --budget 500   # the roster to aim for
fantabot asta legality --rosa "1,2,3"           # which of the 11 schemi this rosa fields
fantabot asta live --league <id> --db <shard> --team <id>    # advise off a live room
fantabot asta bid  --league <id> --db <shard> --team <id> --user <id>

# The seed, the landing zone and the listone bridge all default to the harvest home
# (~/.fantabot/aste_live, or $FANTABOT_HARVEST_DIR). Naming them explicitly overrides the
# home and resolves against the working directory, which is how one evening ends up with
# two landing zones and two checkpoints that never meet.
fantabot harvest scan                                        # which auctions are live
fantabot harvest collect --pool 800                          # subscribe, append to disk
fantabot harvest load --follow                               # landing zone -> Postgres
fantabot harvest backfill events.jsonl                       # a recorded evening

fantabot db check                    # health, per-table row counts and sizes
fantabot db scrape quotazioni        # also statistiche, voti
fantabot db price --system mantra --top-n 15

fantabot auth login                  # interactive; stores each lega's token encrypted
fantabot auth status                 # stored / expires / state, per lega — works with no key
fantabot auth forget --league <id>   # one row at a time, no --all
fantabot auth fantalab-login         # headed, manual; session encrypted into Postgres

fantabot news fetch --write          # the weekly sentiment run

fantabot lineup show                 # the saved XI for a competition. Read-only
fantabot lineup plan                 # the best legal formation for this matchday
fantabot lineup submit               # two locks, dry run by default
fantabot lineup refresh              # bring the history up to date before projecting
fantabot lineup backtest             # Gate 1 over three recorded seasons. Long
fantabot lineup shadow-report        # fielded vs what the shadow would have fielded

fantabot lega sync --write           # read the whole lega and store it
fantabot lega show                   # the latest stored capture, per table

fantabot config-check                # resolved settings, secrets masked
fantabot mantra-grid --write         # one-off, collects the Mantra schema grid
```

## Layout

Four layers, dependencies pointing inward, enforced over every module by
`tests/test_layers.py`:

```
src/fantabot/
  domain/        pure decisions — asta, classic, lega, lineup, harvest, news, mantra,
                 tokens, shared
  application/   use cases — asta_planner, lineup_planner, lineup_submit, lega_sync,
                 harvest_loader, news_fetcher, auth_login, …
  adapters/      the outside world — persistence, http, agent, browser, files, tokens,
                 scraping
  interface/     typer only; the root app and the one Console
  config.py      settings; the one module both sides may read
  data/          the Mantra schema grid, the legality matrix and the slot order, as
                 package data
```

The rule that pays for itself: nothing in `domain/` may reach sqlalchemy,
Playwright, httpx, the agent SDK, typer, rich, the settings or the CLI. That is why
the default test tier runs in about thirty seconds over 3,718 tests (measured
2026-09-24), opens no socket and makes no agent call.

## Safety

`FANTABOT_AUTO_ACT=false` by default (`.env.example`) — every action logs what
it *would* do without clicking anything real. Flip to `true` only after
verifying selectors against the live site in a low-stakes matchday.

## Scheduling

- **Auctions (iniziale / riparazione)**: live sessions, not a point in time.
  `fantabot asta bid` is a long-lived polling loop, started shortly before the
  scheduled auction and left running for its duration — not fired once from cron.

Example crontab. cron gets no shell profile, so the conda env's binary is named
in full rather than relying on an activated environment:
