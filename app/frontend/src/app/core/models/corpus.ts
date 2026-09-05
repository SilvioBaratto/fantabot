/** One format's row of the corpus panel — `AsteRepository.corpus_summary`. */
export interface CorpusFormat {
  asta_type: string;
  rooms: number;
  rooms_with_events: number;
  events: number;
  assignments: number;
  assignments_with_buyer: number;
  assignments_with_player: number;
  /** What `clearing_sales` returns: the only number a plan is ever built on. */
  planner_sales: number;
}

export interface Corpus {
  ok: boolean;
  /** The league shape `planner_sales` was counted under; rendered beside the number. */
  num_credits: number;
  num_teams: number;
  formats: CorpusFormat[];
  error: string | null;
}

/**
 * The registry beside the corpus: what a scan added, before anything was collected.
 *
 * Deliberately a separate panel and never summed with the corpus — a seed grows on
 * every scan whether or not one frame was received.
 */
export interface SeedPanel {
  ok: boolean;
  path: string;
  exists: boolean;
  mtime: string | null;
  rows: number;
  /** Per-format split, counted after the merge the scan wrote. */
  formats: Record<string, number>;
  /**
   * What a collect would use for `--pool` if it were not told otherwise.
   *
   * Carried from the server so the pre-fill can be `max(default, rows)` without this
   * file hardcoding a constant that has already moved once — it was 250 on the evening
   * the population was 649.
   */
  default_pool: number;
  error: string | null;
}
