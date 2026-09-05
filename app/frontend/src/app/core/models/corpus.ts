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
