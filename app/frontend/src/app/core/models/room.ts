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
}
