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
  /**
   * Credits already gone on lots the plan never named, and the evening's ceiling for
   * them. Written since `44cfe89` — an ancestor of this model's own commit — and read by
   * nothing until 1.2. An aggregate cap the operator cannot see after the evening is one
   * they only find out about by not understanding why a bid was held.
   */
  bargain_spent: number | null;
  bargain_allowance: number | null;
  /**
   * The exception type of a poll that raised. Without it a `waiting` row and an `error`
   * row are the same row of nulls — and telling a skipped poll from a crash is the whole
   * purpose of writing an error row at all.
   */
  error: string | null;
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
  /**
   * Where to resume a **follow** from — sent straight back as `?since=`.
   *
   * The last row *parsed*, never the file's line count: the journal flushes per line, so
   * a line caught mid-flush is skipped and counted this poll and parses the next one. A
   * viewer that counted its own rows instead would step over the cycle that line belongs
   * to and drop it from the evening's only record, silently.
   *
   * Zero in page mode, deliberately: a page does not tail, and one field with two
   * meanings is how a paging client comes to send back a position that means something
   * else.
   */
  next_index: number;
  rows: JournalRow[];
  error: string | null;
}
