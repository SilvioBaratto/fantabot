export interface PlanPlayer {
  player_id: string;
  nome: string;
  /**
   * The observed **mean clearing price** across the recorded corpus of this league shape.
   * What the market paid — not what he is worth to us. For as long as this page existed it
   * was the only number on it, under the heading "Price", which reads as advice.
   */
  price: number;
  /**
   * The *prezzo di rinuncia*, **in credits** — `lot_reference` + `lot_ceiling`, the pair the
   * live room prices a lot with.
   *
   * It was `reservations`' marginal until 1.12: an *objective* difference clamped by the
   * budget and never converted to credits. On the live pool that put 140.5 next to a corpus
   * price of 71.8, and 0.0 next to 96.2 for five of thirty plan members.
   *
   * `null` means not priced (an owned player), never zero: a zero is a real answer — "a
   * substitute exists, the rosa does not improve by buying him" — and carries its own
   * provenance. Rendering the two alike is defect B2 restated.
   */
  walk_away: number | null;
  /** Where that number came from. Beside it, never behind a hover. */
  walk_away_provenance: string;
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

/**
 * Why the page shows what it shows. Seven answers, seven screens — every one of them used
 * to be `found=false` under "No plan yet" and a suggestion to sync the lega, which is the
 * right remedy for exactly one of them.
 */
export type AstaPlanOutcome =
  | 'planned'
  | 'no_lega'
  | 'no_sentiment'
  | 'no_corpus'
  | 'empty_pool'
  | 'infeasible'
  | 'unreachable';

export interface AstaPlan {
  found: boolean;
  outcome: AstaPlanOutcome;
  /** What went wrong and what to do about it. Empty only on `planned`. */
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
