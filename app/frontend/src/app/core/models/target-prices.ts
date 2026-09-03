export interface TargetPrice {
  id: string;
  nome: string;
  squadra: string;
  role: string;
  macro_role: string;
  qi: number;
  prior_media_fantavoto: number | null;
  predicted_pct_delta: number | null;
  team_factor: number;
  target_price: number;
  flags: string;
}

export interface Fade {
  role: string;
  observations: number;
}

export interface TargetPricesReport {
  found: boolean;
  system: string;
  stored: number;
  fades: Fade[];
  biggest_bumps: TargetPrice[];
  biggest_cuts: TargetPrice[];
  flag_counts: Record<string, number>;
}
