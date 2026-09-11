export interface LineupPlayer {
  player_id: number;
  nome: string;
}

export interface LineupPlan {
  found: boolean;
  /**
   * Five answers. This route already carried a `reason` and looked solved — but every
   * failure produced the *same* one, "Not connected, or no lineup available yet", so a
   * network blip and a token the platform refuses read identically and only one of them
   * is worth waiting out.
   */
  outcome: 'planned' | 'no_credential' | 'no_lineup' | 'refused' | 'unreachable';
  reason: string | null;
  module: string;
  matchday: number | null;
  starters: LineupPlayer[];
  bench: LineupPlayer[];
}

/**
 * What `POST /lineup/submit` answers. `not_armed` is a **success**: a dry run is what the
 * caller asked for unless it said otherwise, and it carries the XI it would have sent.
 */
export type SubmitOutcome =
  | 'submitted'
  | 'not_armed'
  | 'no_matchday'
  | 'all_modules_refused'
  | 'no_credential'
  | 'refused'
  | 'unreachable';

export interface SubmitResult {
  outcome: SubmitOutcome;
  /** Empty on `submitted`. Names **every** shut lock on `not_armed`, not just the first. */
  reason: string;
  module: string;
  matchday: number | null;
  starters: LineupPlayer[];
  bench: LineupPlayer[];
  /** True only when a lineup actually reached the platform. */
  submitted: boolean;
  /** Read back afterwards: what the platform kept, not what we sent. */
  saved_starters: number | null;
  saved_at: string | null;
  /** `module (code)` for each module refused before one stuck — a `LUP009` walk-down. */
  rejected: string[];
  /** The `mstr` that looks past kickoff. A warning carried *alongside* a submit. */
  past_deadline: string | null;
  /**
   * Why the confirming read-back failed, when it did. Non-empty means the lineup reached
   * the platform — `submitted` is true — and could not then be read back to prove it, so
   * `saved_starters` is not evidence and must not be shown as though it were.
   */
  unconfirmed: string;
}

/** A scheduled run's state — decided once, in `application/lineup_submit.run_record`. */
export type LineupRunStatus = 'submitted' | 'unconfirmed' | 'skipped' | 'failed';

/** One run of the scheduled lineup job, as `GET /lineup/runs` returns it. */
export interface LineupRun {
  /** ISO 8601 with its offset. */
  at: string;
  league: number;
  scheduled: boolean;
  status: LineupRunStatus;
  /** The refusal code or the exception class; empty on a clean submit. */
  code: string;
  /** The sentence behind `code` — which lock is shut, which matchday started when. */
  detail: string;
  module: string;
  matchday: number | null;
  serie_a_matchday: number | null;
  starters: string[];
  bench: string[];
  rejected: string[];
}

/** The scheduled job's history. Read-only: the app writes nothing there and controls nothing. */
export interface LineupRuns {
  ok: boolean;
  /** The file actually read, so an empty screen says where it looked. */
  path: string;
  exists: boolean;
  total: number;
  skipped: number;
  runs: LineupRun[];
  error: string | null;
  last_at: string | null;
  last_age_hours: number | null;
  /** The newest run is older than a normal night — the job may not be running at all. */
  stale: boolean;
}
