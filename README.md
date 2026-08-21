# fantabot

100% autonomous fantacalcio manager for `leghe.fantacalcio.it`: weekly lineup
submission, asta iniziale (initial auction), asta di riparazione (repair
auction) — all handled without a human clicking anything, once the site's DOM
is mapped and a stats source is wired in.

## Status

Scaffold + decision engine done and tested. **Not yet live-capable** — see
"Known unknowns" in `CLAUDE.md`. Two things block real autonomy:

1. `leghe.fantacalcio.it`'s DOM isn't mapped (login form, roster page, asta
   room) — `lineup.py` / `auction.py` raise `NotImplementedError` at the
   site-touching functions until that's done.
2. No stats/injuries/probable-lineup source is wired in yet — implement
   `data_sources.StatsSource` once one is picked.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env   # fill in LEGA_EMAIL / LEGA_PASSWORD / LEGA_URL
fantabot auth          # interactive, opens a real browser — log in once
fantabot config-check   # sanity check env is loaded
pytest                  # strategy.py's decision logic, no live site needed
```

## Commands

```bash
fantabot auth            # one-time interactive login, saves data/storage_state.json
fantabot config-check    # print resolved settings
fantabot lineup-submit   # single run — blocked until data source + DOM selectors are in
```

`auction.watch_and_bid(...)` isn't wired into the CLI yet (needs a
`StatsSource` and the auction DOM). Once both exist, add a `fantabot
auction-watch` command that calls it.

## Safety

`FANTABOT_AUTO_ACT=false` by default (`.env.example`) — every action logs what
it *would* do without clicking anything real. Flip to `true` only after
verifying selectors against the live site in a low-stakes matchday.

## Scheduling

- **Weekly lineup**: a single cron/launchd tick per matchday is enough —
  `fantabot lineup-submit` checks the deadline and no-ops if already
  submitted or too early. Run it a few times daily in the days before each
  deadline.
- **Auctions (iniziale / riparazione)**: these are live sessions, not a single
  point in time — `auction.watch_and_bid` is a long-lived polling loop
  (5s interval) meant to be started shortly before the scheduled auction and
  left running for its duration, not fired once from cron.

Example crontab (adjust path once `.venv` location is final):

```cron
# check for an open lineup deadline 3x/day
0 8,14,20 * * * cd "/Volumes/External SSD/fantabot" && .venv/bin/fantabot lineup-submit >> data/cron.log 2>&1
```
