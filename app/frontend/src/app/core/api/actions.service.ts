import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ActionsService {
  private readonly http = inject(HttpClient);

  runLegaSync(leagueId: number): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}actions/lega-sync?league_id=${leagueId}`,
      {},
    );
  }
}
