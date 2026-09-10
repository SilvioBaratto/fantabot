import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { LineupPlan, SubmitResult } from '../models/lineup';

@Injectable({ providedIn: 'root' })
export class LineupService {
  private readonly http = inject(HttpClient);

  getPlan(leagueId: number): Observable<LineupPlan> {
    return this.http.get<LineupPlan>(`${environment.apiUrl}lineup/plan?league_id=${leagueId}`);
  }

  /**
   * Build the XI and submit it — or, with `arm: false`, say what it would have sent.
   *
   * `arm` is always passed explicitly and is never stored anywhere: the server requires it
   * (a request that omits it is a 422), and this method has no default so a caller cannot
   * inherit a decision from the last one.
   */
  submit(leagueId: number, arm: boolean): Observable<SubmitResult> {
    return this.http.post<SubmitResult>(`${environment.apiUrl}lineup/submit`, {
      league_id: leagueId,
      arm,
    });
  }
}
