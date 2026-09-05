# CLAUDE.md — `app/` (fantabot-app)

Guidance for Claude Code when working in `app/`. **This file is the app's own record** —
the rules below are the ones a reader cannot infer from the code. There is no app spec
to point at: the root `SPEC.md` holds only the phase in flight and was overwritten by
later ones, and closed phases are archived under `tasks/archive/` (maintainer-only, and
git-ignored, so a checkout does not carry them). `todo/TODO.md` holds the open work.

## What this is

`fantabot-app` — a local, single-user web UI over the `fantabot` project. One command
(`fantabot-app`) provisions Postgres (no Docker), runs the FastAPI adapter, and serves the
compiled Angular bundle on one port. Installed with **uv**; the only prerequisite is `uv`
itself (Postgres ships inside the `pixeltable-pgserver` wheel; the frontend is compiled
into the wheel, so end users need no Node).

There is **no Docker** (the compose/Dockerfile scaffold was removed), no auth/JWT of its
own, no BAML. The clean architecture lives in `fantabot`; this is a thin adapter over it.

## Layout

```
app/
  pyproject.toml            # the fantabot-app package (hatchling); fantabot = path dep
  uv.lock                   # committed — reproducible install
  fantabot_app/             # ONE importable package
    cli.py                  # Typer launcher: setup / up / stop / doctor (up = default)
    paths.py                # ~/.fantabot/{pgdata,logs}
    server.py               # serve API + SPA on one port, open browser
    doctor.py               # environment checks
    provisioner/            # postgres (pixeltable_pgserver) + migrate + chromium
    api/                    # the FastAPI adapter (was top-level `app`, renamed in R1b)
      main.py  infrastructure/{settings,database,jobs,orm}  v1/{router,endpoints}  schemas/
      tests/                # api tests (pytest)
    web/                    # compiled Angular bundle (git-ignored build artifact; in the wheel)
  frontend/                 # Angular 21 (Tailwind v4, signals, standalone, OnPush)
  scripts/build_frontend.py # ng build -> fantabot_app/web (run before install / in CI)
  tests/                    # launcher + fitness + doctor tests
```

## Commands

```bash
# Python — dedicated venv (keep the fanta conda env fantabot-only)
uv venv                                   # app/.venv
uv pip install -e ".[dev]"
app/.venv/Scripts/python -m pytest        # from app/ : launcher + api tests (zero sockets;
                                          #   integration + real-DB tests are -m integration)
app/.venv/Scripts/python -m ruff check fantabot_app tests
app/.venv/Scripts/python -m mypy fantabot_app   # strict; tests + scaffold main.py excluded

# Frontend
cd frontend && npm ci && npm start        # dev server (dev only)
python scripts/build_frontend.py          # build + stage into fantabot_app/web for the wheel
cd frontend && npx ng test --watch=false  # vitest
```

## Rules

- **One DB, one engine.** Every session comes from `fantabot.adapters.persistence.database_manager`
  (`get_db` in `api/infrastructure/database.py`). The API adapter builds no `create_engine`/
  `sessionmaker` — enforced by `tests/test_fitness.py` (A6). The provisioner's transient
  admin engine (CREATE DATABASE) is the one documented exception.
- **Degrade open.** Read endpoints never 500 on a missing token/DB — they return an empty/
  not-connected state, like the CLI.
- **No secret leaks.** The app never reads a plaintext token or calls decrypt — fantabot
  does, internally (A7 fitness test). No token in any response.
- **Thin adapter.** Endpoints call fantabot use cases/repos; no re-added domain/application
  hexagon here.
- **The app never acts. It reads, and it watches.** No live bid, no lineup submission —
  and that is now a settled decision rather than a v1 deferral: the operator chose "the
  CLI bids, the app watches", and the app is loopback-only. The bidding locks
  (`FANTABOT_AUTO_ACT` + `--arm`, first Ctrl-C disarms) are a terminal contract a browser
  tab cannot reproduce — a closed tab would leave a bidding thread running with nobody
  attached.
  The fitness test bans the **acting** names: `teamLineup_submit(`, `decide_bid(`,
  `place_raise(`, `run_bid_loop(`, `RoomTracker(`. It used to ban the string
  `application.asta_room`, which is the module that also holds the read-only
  `resolve_room` and `RoomFrame` — so a room *viewer* failed the guard while
  `rtdb.place_raise` and `room.run_bid_loop`, the two functions that actually spend
  credits, were absent from the list and passed. The guard banned the viewer and
  permitted the bidder; it now does the opposite.
- **A FantaLab bearer never enters app code.** `rest.fetcher_from(store)` and
  `LiveAuctionsClient.from_store(store)` resolve it inside the adapter and keep it in a
  closure, mirroring `apileague.auth_headers(league_id, store=...)`. Callers hand over the
  store. This is not covered by the A7 fitness test — that scans only for
  `load_plaintext` and `.decrypt(`, and `FantalabStore.load()` trips neither — so it is a
  rule kept by review, which is why it is written here.
- **Actions run as jobs.** Long/interactive use cases (login, sync, news) run on the
  in-process job runner (`api/infrastructure/jobs.py`); the UI polls `GET /jobs/{id}`.
  `GET /jobs` is the source of truth a page reattaches from, and `?since=N` is how a
  1.5 s poll costs what happened since the last one instead of the whole log. Both exist
  because the job id used to live only in Angular component state: a refresh mid-run
  orphaned the job invisibly *and* re-enabled the button that starts a second one.
- **One harvest home, and it is derived.** `fantabot.config.harvest_dir()` —
  `~/.fantabot/aste_live` by default, `FANTABOT_HARVEST_DIR` when exported — holds
  `live.jsonl`, its `.offset` and `.state`, `seed.json` and `listone_map.json`. The app
  addresses it through that function and never through a relative path: `./data/aste_live/`
  resolves only from the repository root, and the app's working directory is wherever its
  launcher was started, so a collector started from the app and a `harvest load` typed in a
  terminal used to address two different landing zones. Same argument that made the bundled
  database canonical. **Set it as a real exported variable, never in `.env`** — a `Settings()`
  reads `.env` relative to the working directory, which is the split, not the fix.
- **The role lock has two roles, not one.** `adapters/files/lock.py`, an OS advisory lock
  per role on `<landing>.<role>.lock`: `COLLECTOR` and `LOADER`. Two collectors double
  every record and two loaders corrupt each other's ladders, but collector-plus-loader is
  the *intended* pairing, so one lock would forbid the normal case. The property being
  bought is that the OS releases it however the holder dies — so "is a collector running?"
  needs no pid check and stays correct across an app restart, which is what makes the two
  rules below implementable at all. Its Windows backend is written and unverified
  (`todo/TODO.md` §4).
- **The supervisor is a subprocess, and cancellation is not the reason.**
  `api/infrastructure/processes.py`. `adapters/files/landing.py` states the invariant: a
  frame that never reached disk is gone, and an evening of auctions does not come back. A
  daemon thread dies with the server, and `jobs.py` accepts that on the grounds that every
  fantabot write is an upsert — true of the loader, **false of the collector**.
  `application/harvest_loader.py` also records a pass holding ~17x its 32 MB window (1.6 GB
  resident measured), which inside the SPA process means the UI stalls on a timer. It was
  proven against `harvest load --follow` before it was pointed at `collect`: the loader is
  idempotent and restartable, the collector is the thing that cannot be re-run, and
  debugging process control against the irreplaceable one is the wrong order.
  **Stop sequence:** `SIGINT` to a real pid — not `SIGTERM`, which has no handler on that
  path; `aste_collect` catches only `KeyboardInterrupt` — then poll the role lock at 250 ms
  for 15 s, then `SIGKILL`.
- **The app never resets a load checkpoint.** It shows the offset, the file size and the
  lag, and it names the command. The Classic recovery *was* an offset reset re-reading
  1.31 GB; it is the highest-value action in the feature and the only destructive-shaped
  one, and it stays a terminal act. There is nothing in `POST /harvest/load` that could do
  it — the offset is the loader's.
- **`fantabot-app stop` refuses while a collector holds the lock**, names the role and the
  landing zone, and offers `--force`. A three-hour asta evening is exactly when a stray
  `stop` costs records, and the landing zone's guarantee is about kills it did not choose.
  `POST /harvest/collect` refuses in the same spirit when `pool` is below the seed
  population, naming both numbers: a pool below the population is silent starvation, since
  a watcher on a live evening does not finish, so a queued auction never gets a permit and
  never connects at all. That cost 145 of 395 auctions on 2026-08-27. The CLI warns and
  continues, which is right at a terminal where someone reads the warning; from a browser
  at 21:00 it is a three-hour run that quietly follows two thirds of an evening.
- **Collection-time filters are the app's, never.** `harvest scan` exposes no `--only` and
  `harvest collect` no format selector — `from_seed_row` reads each row's own `asta_type`,
  so one seed carries both. Filtering is a query. The poller filtering to Mantra is what
  threw away 85% of the population. A *load* takes a format, because that is a read.
- **The room journal is the CLI's record, and the screen says so.**
  `GET /asta/journal` pages `data/room_journal.jsonl` newest-first; the app writes nothing
  there. It resolves the path absolute deliberately — `fantabot_data_dir` defaults to
  `./data`, so "there is no journal" and "you are looking in the wrong place" are the same
  screen until it says where it looked. A line that does not parse is skipped *and
  counted*: the journal flushes per line, so the one line a crash can tear is the newest,
  and tail-first that is the first row drawn.
- **The bundled server's lifetime is explicit.** `PostgresProvisioner` passes
  `cleanup_mode=None` on every path, so Postgres outlives whatever started it and only
  `fantabot-app stop` / `fantabot-app db stop` takes it down. pgserver's default is
  `'stop'` — an atexit hook that stops the server when the last handle-holding process
  exits — which would make `db start` print a DSN to a server that died with the command.
  A fitness test reads the AST and fails on any `get_server(` call without an explicit
  `cleanup_mode`, because `get_server` caches per pgdata and *ignores* the argument on a
  cache hit: one bare call anywhere decides the mode for the whole process.
- **An exported `FANTABOT_DATABASE_URL` wins, and `.env` does not.** `start()` returns it
  and provisions nothing. The asymmetry is the point: an export is an instruction from the
  operator, while a `.env` found by whatever the working directory happens to be is the
  mechanism that put the CLI and the app on two different databases. `db stop`,
  `db status` and `db create` ignore it and address `~/.fantabot/pgdata` — `db start`
  prints an `export` line the operator pastes, and a `db stop` that honoured it would be a
  no-op in exactly that shell.
- **Disconnect purges; reconnect restores.** `DELETE /auth/league/{id}` removes the lega
  whole, across six tables — removing the token alone left it on every screen but the one
  that said it was disconnected. Reconnecting the account brings the lega back (its data
  on the next sync) and **that is correct**: the operator reconnected the account, so a
  durable exclusion would be a second, invisible piece of state. The CLI's `fantabot auth
  forget --league <id>` deliberately still removes the token alone — its one-row-at-a-time,
  no-`--all` contract is published in `README.md` and pinned by tests.
- **Headed login stays manual.** `POST /auth/login` opens the real browser and the user
  signs in by hand; a per-job gate + `.../confirm` is the web "press Enter".
```
