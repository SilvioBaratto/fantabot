/** `sroles=1` is Classic, `sroles=2` is Mantra; null when the lega stated neither. */
export type LegaFormat = 'classic' | 'mantra';

export interface LegaOverview {
  league_id: number;
  league_name: string | null;
  captured_at: string | null;
  /** Optional so fixtures written before the field existed still type-check; the API
   * always sends it, and a missing value reads as unknown, never as Mantra. */
  format?: LegaFormat | null;
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
