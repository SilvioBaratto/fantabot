/**
 * One player kept out of every asta plan, with the reason and the source.
 *
 * The listone lags reality: fantacalcio.it still carried Rafael Leao at MIL, quotazione
 * 18, the day after his permanent transfer to Galatasaray. Nothing else in the engine can
 * drop him — the scraper reproduces the site, and the sentiment gate is floored so news
 * tilts a value and never vetoes it.
 */
export interface Exclusion {
  player_id: number;
  /**
   * The player's name on `players`, or `null` when this database has never scraped that
   * id. `null` rather than `'?'`: an id nothing resolves is either a typo to delete or a
   * season nobody has scraped, and only the first is a mistake. The page renders the two
   * differently because the remedies differ.
   */
  nome: string | null;
  /** Free text, for a human. What happened and when — never "excluded". */
  reason: string;
  /** Where the claim came from. Empty is legal; the reason is the required half. */
  source: string;
}

export interface Exclusions {
  exclusions: Exclusion[];
  /**
   * Why the list could not be read at all. `null` on success — **including on a genuinely
   * empty table**, which is the true answer for a fresh install. An empty list with no
   * error means every player on the listone is buyable; an empty list because Postgres
   * would not open is a different fact with a different remedy, and rendering the two
   * alike is the `found=false` defect this phase removed from four other screens.
   */
  error: string | null;
}

/** What the form sends. `source` is optional, as `db exclude --source` has always been. */
export interface ExcludeRequest {
  player_id: number;
  reason: string;
  source?: string;
}

/** What a write leaves behind: the row, and the list it is now part of. */
export interface ExclusionWritten {
  recorded: Exclusion;
  /** The refreshed list, so the page renders what the table holds, not what it hopes. */
  exclusions: Exclusion[];
  total: number;
}
