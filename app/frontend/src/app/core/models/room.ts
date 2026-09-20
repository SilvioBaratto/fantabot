/**
 * The five answers `GET /asta/room` can give. Five names, not one flag:
 * T31 (`tasks/BACKLOG.md`) records what a single label over four different failures costs,
 * and only one of these is fixed by going back to the room.
 */
export type RoomOutcome = 'resolved' | 'refused' | 'bad_link' | 'no_credential' | 'unreachable';

export interface RoomCheck {
  outcome: RoomOutcome;
  reason: string;
  fantaleague_id: string | null;
  shard: number | null;
  asta_type: string | null;
  asta_mode: string | null;
  raise_mode: string | null;
  num_teams: number | null;
  num_credits: number | null;
  seat_team_id: string | null;
  seat_team_name: string | null;
  /**
   * **Ours**, from the stored FantaLab session — the uid a bid payload is signed with.
   * Carried on this response because `POST /asta/room/bid` needs it and resolving the room
   * a second time to learn it would be a second resolution path with a second set of
   * outcomes. Not a credential: the bearer is resolved inside the adapter and never leaves
   * it.
   */
  seat_user_id: string | null;
  roster_size: number | null;
  /** `read from the room` / `assumed — nothing was declared`. Rendered beside the size. */
  roster_provenance: string;
}

/**
 * The five answers `POST /asta/room/bid` can give.
 *
 * Four of them are `RoomOutcome`'s, reached through the same `check_room` call rather than
 * re-derived — a second resolution path is a second set of reasons, and they drift.
 * `started` covers both an armed run and a dry one, because a dry run is not a failure: it
 * is the rehearsal an operator does before arming, and it still watches, decides and
 * journals.
 */
export type BidOutcome = 'started' | 'refused' | 'bad_link' | 'no_credential' | 'unreachable';

export interface BidStarted {
  outcome: BidOutcome;
  reason: string;
  /** Empty on every outcome but `started`. */
  job_id: string;
  armed: boolean;
  /**
   * Every shut lock, **by name**, in the order an operator would fix them. Empty when
   * armed. A list and not a sentence so the page can mark each control; `reason` is the
   * same facts as one line.
   */
  closed: string[];
  /**
   * The roster band this run was started with, and where the number came from.
   *
   * The room check card above already shows both — and until the route sent them to the
   * child, the number on screen and the number the bidder planned and capped against agreed
   * only by coincidence. Echoing it here is what makes the panel a statement about the run
   * rather than about the room.
   */
  roster_size: number | null;
  roster_provenance: string;
}

/**
 * The six answers `GET /asta/advisory` can give.
 *
 * Four are `GET /asta/plan`'s, and for the same reasons: the advisory *is* a plan re-solved
 * after every sale, so it fails where a plan fails. A ledger that will not answer is
 * `unreachable` — rendering that as "no targets" would be a false statement rather than a
 * missing one, at the moment an operator decides they have nothing to chase.
 */
export type AdvisoryOutcome =
  'advised' | 'no_sentiment' | 'no_corpus' | 'empty_pool' | 'infeasible' | 'unreachable';

export interface AdvisoryTarget {
  player_id: string;
  nome: string;
  walk_away: number;
  /**
   * False when the walk-away is under one credit. `reservations` clamps a negative marginal
   * to zero — which means only that he is freely replaceable — and the bidder refuses at
   * every price, because its smallest raise is `current + step`. A row saying "chase,
   * walk-away 0" names the one thing the system will not do. He stays on the list: he is in
   * the target roster and the operator should see him.
   */
  chase: boolean;
}

export interface AdvisoryOpponent {
  team_id: string;
  players: number;
  spent: number;
  remaining: number;
}

export interface Advisory {
  outcome: AdvisoryOutcome;
  reason: string;
  targets: AdvisoryTarget[];
  opponents: AdvisoryOpponent[];
  sales: number;
  /** Sales the listone could not name. Each is a purchase nobody subtracted. */
  dropped_sales: number;
  total_cost: number;
  objective: number;
}
