# CLAUDE.md — `app/` (fantabot-app)

Guidance for Claude Code when working in `app/`. **This file is the app's own record** —
the rules below are the ones a reader cannot infer from the code. There is no app spec
to point at: the root `SPEC.md` holds only the phase in flight and was overwritten by
later ones, and closed phases are archived under `tasks/archive/` (maintainer-only, and
git-ignored, so a checkout does not carry them). `tasks/BACKLOG.md` holds the open work.

## What this is

`fantabot-app` — a local, single-user web UI over the `fantabot` project. One command
(`fantabot-app`) provisions Postgres (no Docker), runs the FastAPI adapter, and serves the
compiled Angular bundle on one port. Installed with **uv**; the only prerequisite is `uv`
itself (Postgres ships inside the `pixeltable-pgserver` wheel; the frontend is compiled
into the wheel, so end users need no Node).

**The target is parity with the CLI.** The app is a friendlier way to drive the same use
cases — every `fantabot` command has, or is meant to have, a screen, the acting ones
included. It is not a read-only companion to the CLI, and a command with no screen is a
gap rather than a boundary. See *The app mirrors the CLI* under Rules for what that does
and does not license.

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
      main.py  infrastructure/{settings,database,jobs,processes,...}  v1/{router,endpoints}
      reads/                # read models the endpoints render
      tests/                # api tests (pytest)
      #  No `orm/` and no `schemas/`: deleted in T43. Both were scaffold. `Base` was an
      #  empty DeclarativeBase, so create_all/drop_all built and dropped zero tables, and
      #  no route declares Depends(get_db), so the test override overrode nothing.
      #  Persistence is fantabot's; this layer holds no models of its own.
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
- **The app mirrors the CLI, acting included.** Reversed 2026-09-07 at the operator's
  request. The rule here used to read *"the app never acts — it reads, and it watches"*
  and banned the live bid and the lineup submission outright; the app is instead meant to
  be the CLI with a face on it, so a command `fantabot` has is a command the app is
  expected to grow. Parity is the target and a missing screen is a gap, not a boundary.
  **What the old rule was protecting is not the ban.** Two facts sat under it, both still
  true, and both survive as constraints on *how* an acting path is built rather than on
  whether it is:
  **A browser tab is not a process.** `FANTABOT_AUTO_ACT` + `--arm` + "first Ctrl-C
  disarms" is a contract held by a terminal someone is sitting at. A closed tab over a
  daemon thread leaves a bidding loop running with nobody attached — the failure the old
  rule bought its way out of by forbidding the feature. So an acting path runs where a
  stop is real: the supervised subprocess of `api/infrastructure/processes.py`, with the
  job registry's `stop`, the OS role lock and the documented SIGINT → poll → SIGKILL
  sequence. That machinery exists (it was built for the collector, T10–T12) and is the
  precedent to follow, not one to invent beside.
  **The two locks stay two locks, and the browser supplies the second.**
  `FANTABOT_AUTO_ACT` in the environment is unchanged — process-wide `.env` state, opted
  into in the morning. `--arm` becomes an explicit per-run arm carried in the request:
  never a stored setting, never a default, never remembered across a reload, because the
  property being bought is that the operator who armed it is the one watching. A disarm
  must be reachable from the page **and** must not depend on the page staying open — a
  reload that cannot find the running bid is the same accident as the closed tab.
  **`tests/test_fitness.py` still bans the acting names** — `teamLineup_submit(`,
  `decide_bid(`, `place_raise(`, `run_bid_loop(`, `RoomTracker(`, with
  `test_the_boundary_names_the_functions_that_actually_act` pinning that list. They are
  the old rule's enforcement and are **deliberately not retired in this edit**: they come
  out in the same commit that builds the first acting path, so the guard is never green
  over a feature nobody wrote. Until that commit the app is still read-only in fact, and
  this rule states the intent, not the state.
  The one piece of that guard worth keeping whatever replaces it: it bans the **write
  path**, not a module name. It used to ban the string `application.asta_room`, which
  also holds the read-only `resolve_room` and `RoomFrame` — so a room *viewer* failed
  while `rtdb.place_raise` and `room.run_bid_loop`, the two functions that actually spend
  credits, were absent from the list and passed. Whatever arming check replaces it must
  name the functions that spend, not the modules they live in.
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
  rules below implementable at all. Its Windows backend is written and was
  unverified for as long as `app-ci`'s path filter was `app/**` — the one Windows runner
  never triggered on the file. T42c put `lock.py` in the filter, so it is now exercised
  on every change to it (`tasks/BACKLOG.md`, T42).
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
- **The app does not reset a load checkpoint, because the CLI has no flag that does.**
  Under the mirror rule this is parity, not a ban: `harvest load` exposes no `--reset`,
  the Classic recovery *was* an offset reset re-reading 1.31 GB and it was performed by
  deleting the `.offset` by hand. So the app shows the offset, the file size and the lag,
  and names the command; there is nothing in `POST /harvest/load` that could do it,
  because the offset is the loader's. Give the CLI the flag and the app gets the button —
  and give it the flag first, since the destructive-shaped act should be spelled out in
  one place before it is wired to two.
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
