/**
 * One recorded collector log the picker may offer — a **name**, never a path.
 *
 * The route takes the name and refuses any the candidate list does not hold. That is
 * deliberate and it is the whole reason this is a picker: naming a harvest path explicitly
 * *creates* what it cannot find, which is how a second landing zone with its own
 * checkpoint and its own fold state appears. Three stray files already sit in the real
 * home from exactly that.
 */
export interface LogRow {
  name: string;
  bytes: number;
  mtime: string | null;
  /**
   * The active landing zone. Offered and flagged, never hidden: a backfill only reads it
   * and every write is an upsert, so the offset — which belongs to `harvest load` — has
   * nothing to fear. Hiding it would make the one file with 1.3 GB in it unreachable.
   */
  live: boolean;
}

/** One scan seed, with how many auctions it describes. */
export interface SeedRow {
  name: string;
  /**
   * The only number that tells today's seed from a recorded evening's *before* the run.
   * A mismatched pair is silent: every auction the seed does not describe is counted
   * `unknown auction` and dropped, and the run still reports success.
   */
  rows: number;
  mtime: string | null;
}

/**
 * What a backfill may be pointed at, read from the harvest home.
 *
 * `exists: false` is not two empty lists. A home nobody has created names a command —
 * `fantabot-app harvest adopt` — and a home that cannot be read names a permissions
 * dialog; `error` carries the second.
 */
export interface BackfillCandidates {
  home: string;
  exists: boolean;
  logs: LogRow[];
  seeds: SeedRow[];
  error: string | null;
}

/** The chosen pair, by name, as `POST /harvest/backfill` takes it. */
export interface BackfillRequest {
  log: string;
  seed: string;
  asta_type: string;
  dry_run: boolean;
}
