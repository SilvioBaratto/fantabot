export interface LegaOverview {
  league_id: number;
  league_name: string | null;
  captured_at: string | null;
  matchday: number | null;
  budget: number | null;
  roster_size: number | null;
  min_roles: number[] | null;
  max_roles: number[] | null;
  modules: string[] | null;
  bench_size: number | null;
  team_count: number;
}

export interface RosterSlot {
  player_id: number;
  cost: number | null;
}

export interface TeamRoster {
  team_id: number;
  nome: string;
  owner: string;
  credits_initial: number | null;
  credits_spent: number | null;
  credits_remaining: number | null;
  roster: RosterSlot[];
}
