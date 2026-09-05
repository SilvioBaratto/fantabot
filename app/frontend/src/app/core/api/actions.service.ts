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

  /**
   * Ask FantaLab which auctions are live and merge them into the seed.
   *
   * No format argument, and none is offered: filtering is a query, never a decision
   * taken at collection time — the poller filtering to Mantra threw away 85% of the
   * population.
   */
  runHarvestScan(): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(`${environment.apiUrl}actions/harvest-scan`, {});
  }

  runNewsFetch(season: string): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}actions/news-fetch?season=${encodeURIComponent(season)}`,
      {},
    );
  }
}
