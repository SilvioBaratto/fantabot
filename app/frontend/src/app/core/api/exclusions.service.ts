import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  ExcludeRequest,
  ExclusionWithdrawn,
  ExclusionWritten,
  Exclusions,
} from '../models/exclusion';

/**
 * `fantabot db exclusions`, `db exclude` and `db unexclude`, over the wire.
 *
 * The path follows the command (`db/exclusions`) while the screen sits on the Asta page,
 * because that is where the effect shows: an exclusion is invisible everywhere else —
 * the plan it changes does not say a player was removed.
 */
@Injectable({ providedIn: 'root' })
export class ExclusionsService {
  private readonly http = inject(HttpClient);

  list(): Observable<Exclusions> {
    return this.http.get<Exclusions>(`${environment.apiUrl}db/exclusions`);
  }

  /**
   * Record one. The server refuses a blank reason and a non-positive id with a 422
   * carrying the refusal's own wording — the same sentence `fantabot db exclude` prints,
   * because both call `application/exclusions.clean_exclusion`. The page shows that
   * sentence rather than composing its own, which is the whole point of the shared call.
   */
  add(request: ExcludeRequest): Observable<ExclusionWritten> {
    return this.http.post<ExclusionWritten>(`${environment.apiUrl}db/exclusions`, request);
  }

  /**
   * Withdraw one, by id — `fantabot db unexclude`. The server answers **404** when
   * nothing was excluded under that id, carrying the command's own sentence: a delete
   * that matched nothing and reported success is the defect the command was built to
   * remove, and the operator's next act depends on which of the two happened.
   */
  remove(playerId: number): Observable<ExclusionWithdrawn> {
    return this.http.delete<ExclusionWithdrawn>(`${environment.apiUrl}db/exclusions/${playerId}`);
  }
}
