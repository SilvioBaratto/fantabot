import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { BackfillResult, SnapshotRequest, TeamSnapshotResult } from '../models/teams';

/**
 * `fantabot db snapshot-team` and `db backfill-teams`, over the wire.
 *
 * Neither is a job. `JobsService` is for the long and the interactive — a login, the
 * eight-read lega sync, the news fan-out. These are one small GET plus an insert, and
 * one SQL pass, so each answers in its own request.
 */
@Injectable({ providedIn: 'root' })
export class TeamsService {
  private readonly http = inject(HttpClient);

  /**
   * Capture our own team's credits and roster ids. Every call appends a **new** row —
   * a re-run never overwrites the last capture, which is what makes the drift between
   * two captures readable at all.
   */
  snapshot(request: SnapshotRequest): Observable<TeamSnapshotResult> {
    return this.http.post<TeamSnapshotResult>(`${environment.apiUrl}db/snapshot-team`, request);
  }

  /**
   * Resolve club codes to full names. No arguments, as the command has none: `teams` is
   * Serie A's clubs, not a lega's, and the backfill reads the two vocabularies already
   * in Postgres.
   */
  backfill(): Observable<BackfillResult> {
    return this.http.post<BackfillResult>(`${environment.apiUrl}db/backfill-teams`, {});
  }
}
