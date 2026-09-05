/**
 * The room journal — the CLI's record of a live auction evening.
 *
 * `data/room_journal.jsonl` holds 5,192 rows from 2026-09-01 and until this model existed
 * nothing anywhere read it: the audit that found all three bidder defects (B1 the uuid
 * mismatch, B2 the collapsing walk-away, B3 the 41 uncallable players) was done by hand
 * against the file. The app never bids, so this is evidence — not a log of what it did.
 */
export interface JournalRow {
  /** 1-based line number, oldest = 1. Paging must not cost a row its citation. */
  index: number;
  at_ms: number | null;
  node: string | null;
  lot: string | null;
  name: string | null;
  price: number | null;
  /**
   * Null on 4,501 of the 5,192 recorded rows — that is what defect B2 looks like in the
   * file, so it is rendered as a null and never as a zero or a dash that reads like one.
   */
  walk_away: number | null;
  provenance: string | null;
  decision: string | null;
  reason: string | null;
  credits_left: number | null;
  max_cap: number | null;
  /** The count of the rosa, not its 27 ids: the row is a decision, not an inventory. */
  owned_count: number | null;
  /** Added after the 2026-09-01 evening was recorded, so null across all of it. */
  cycle_ms: number | null;
}

export interface JournalPage {
  ok: boolean;
  /** Absolute. `fantabot_data_dir` is relative, so "no journal" needs "where it looked". */
  path: string;
  exists: boolean;
  total: number;
  /** Lines that did not parse. A torn tail is one, and it is shown rather than dropped. */
  skipped: number;
  offset: number;
  limit: number;
  rows: JournalRow[];
  error: string | null;
}
