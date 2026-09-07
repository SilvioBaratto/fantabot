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
