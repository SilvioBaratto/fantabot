export interface PlanPlayer {
  player_id: string;
  nome: string;
  price: number;
}

/**
 * A next-best plan, one per top target lost. The CLI prints three and the page printed
 * none — a single optimal rosa reads as a prescription, and it is not one: the evening
 * takes players off the board and the operator needs to know what the plan becomes.
 */
export interface Fallback {
  total_cost: number;
  objective: number;
}

export interface AstaPlan {
  found: boolean;
  /**
   * Why not, when the endpoint can name it. Without it "run `news fetch` first" and "the
   * database is down" were the same blank screen.
   */
  reason: string | null;
  listone: string;
  roster_size: number;
  total_cost: number;
  objective: number;
  budget: number;
  /** The knobs the CLI exposes as options. `lam` was fixed at 0.0 and untunable. */
  lam: number;
  /** The rosa the plan was built around. It did not exist: every plan was for an empty one. */
  owned: string[];
  /**
   * How many players FantaLab's listone can call, or `null` when it was unreachable and
   * the plan degraded open over the whole pool. Never `0`: an empty exclusion set and an
   * unknown one are different facts, and 41 of 570 players are genuinely uncallable.
   */
  callable_pool: number | null;
  players: PlanPlayer[];
  fallbacks: Fallback[];
}
