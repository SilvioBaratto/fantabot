import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { LegaOverview, TeamRoster } from '../models/lega';

@Injectable({ providedIn: 'root' })
export class LegaService {
  private readonly http = inject(HttpClient);

  getLeagues(): Observable<LegaOverview[]> {
    return this.http.get<LegaOverview[]>(`${environment.apiUrl}lega`);
  }

  getRosters(leagueId: number): Observable<TeamRoster[]> {
    return this.http.get<TeamRoster[]>(`${environment.apiUrl}lega/${leagueId}/rosters`);
  }
}
