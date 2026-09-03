export interface LineupPlayer {
  player_id: number;
  nome: string;
}

export interface LineupPlan {
  found: boolean;
  reason: string | null;
  module: string;
  matchday: number | null;
  starters: LineupPlayer[];
  bench: LineupPlayer[];
}
