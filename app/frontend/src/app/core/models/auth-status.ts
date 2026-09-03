export interface LeagueTokenStatus {
  league_id: number;
  league_name: string | null;
  state: string;
  expires_at: string;
  last_verified_at: string | null;
  user_id: number | null;
  team_id: number | null;
}

export interface FantalabSessionStatus {
  user_id: string;
  captured_at: string;
  last_used_at: string | null;
}

export interface AuthStatus {
  leagues: LeagueTokenStatus[];
  fantalab: FantalabSessionStatus[];
  has_key: boolean;
}
