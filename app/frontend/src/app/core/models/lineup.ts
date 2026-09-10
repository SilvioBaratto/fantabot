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
}
