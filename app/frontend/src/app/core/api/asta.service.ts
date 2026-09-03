import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AstaPlan } from '../models/asta-plan';

@Injectable({ providedIn: 'root' })
export class AstaService {
  private readonly http = inject(HttpClient);

  getPlan(leagueId: number): Observable<AstaPlan> {
    return this.http.get<AstaPlan>(`${environment.apiUrl}asta/plan?league_id=${leagueId}`);
  }
}
