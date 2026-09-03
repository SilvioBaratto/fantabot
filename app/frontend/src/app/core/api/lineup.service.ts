import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { LineupPlan } from '../models/lineup';

@Injectable({ providedIn: 'root' })
export class LineupService {
  private readonly http = inject(HttpClient);

  getPlan(leagueId: number): Observable<LineupPlan> {
    return this.http.get<LineupPlan>(`${environment.apiUrl}lineup/plan?league_id=${leagueId}`);
  }
}
