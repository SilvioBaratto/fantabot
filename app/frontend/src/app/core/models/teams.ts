/**
 * The two one-shot team commands — `fantabot db snapshot-team` and `db backfill-teams`.
 *
 * Both are writes and both **fail closed**: a failure arrives as a named outcome with
 * the refusal's own wording, never as a success-shaped body. The page renders that
 * wording rather than composing its own, because the sentence carries the remedy and
 * the person reading it here is the person who would otherwise read it in a terminal.
 */

/** Which lega to capture. Required — the CLI's `FANTABOT_LEAGUE_ID` fallback exists
 *  because a Typer option needs a default; a page has a field the operator is looking
 *  at, and `league_team_snapshot` is append-only, so a row under the wrong lega stays. */
export interface SnapshotRequest {
  league_id: number;
}

/**
 * `saved` is the capture. The other three are why there is none:
 * `no_credential` — no key, or no token for that lega. `auth login` is the remedy.
 * `refused` — the platform answered and said no. A new login, not a wait.
 * `unreachable` — the network or the database. A wait, not a login.
 */
export type SnapshotOutcome = 'saved' | 'no_credential' | 'refused' | 'unreachable';

export interface TeamSnapshotResult {
  outcome: SnapshotOutcome;
  /** Empty on `saved`; the server's own sentence otherwise. */
  reason: string;
  league_id: number;
  team_id: number | null;
  nome: string;
  owner: string;
  /**
   * `null` rather than `0` when the platform did not send the figure. A `0` here is a
   * claim that the team has spent nothing, which is a different thing from not knowing.
   */
  credits_initial: number | null;
  credits_spent: number | null;
  credits_remaining: number | null;
}

/**
 * `unresolved` is deliberately not `unreachable`. The club-name mapping is fail-closed:
 * a code with no name means nothing was written and the remedy is to scrape that
 * season's voti. Collapsing it into an outage sends the operator to start a database
 * that is already running.
 */
export type BackfillOutcome = 'resolved' | 'unresolved' | 'unreachable';

export interface BackfillResult {
  outcome: BackfillOutcome;
  reason: string;
  /**
   * Rows whose `nome_completo` changed. **Zero is a success**: a fresh database, or one
   * scraped listone-first, has no `match_grain` names to resolve from yet, and the
   * placeholder three-letter codes stay until fixtures exist.
   */
  changed: number;
}
