/**
 * The five answers `GET /asta/room` can give. Five names, not one flag:
 * `todo/TODO.md` §3.4 records what a single label over four different failures costs,
 * and only one of these is fixed by going back to the room.
 */
export type RoomOutcome =
  | 'resolved'
  | 'refused'
  | 'bad_link'
  | 'no_credential'
  | 'unreachable';

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
  /** `read from the room` / `assumed — the room declared nothing`. Rendered beside the size. */
  roster_provenance: string;
}
