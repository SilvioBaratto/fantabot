export interface TargetPrice {
  id: string;
  nome: string;
  squadra: string;
  role: string;
  qi: number;
  target_price: number;
  flags: string;
}

export interface TargetPricesReport {
  found: boolean;
  /**
   * `no_data` is a real answer rather than a failure: the fit needs training seasons of
   * `statistiche` and a fresh install has none. It used to render as an empty table under
   * a confident heading, which reads as "the model says nothing moved".
   */
  outcome: 'priced' | 'no_data' | 'unknown_system' | 'unreachable';
  reason: string | null;
  system: string;
  stored: number;
  biggest_bumps: TargetPrice[];
  biggest_cuts: TargetPrice[];
  flag_counts: Record<string, number>;
}
