import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AstaPlan } from '../models/asta-plan';
import { RoomCheck } from '../models/room';

@Injectable({ providedIn: 'root' })
export class AstaService {
  private readonly http = inject(HttpClient);

  getPlan(leagueId: number): Observable<AstaPlan> {
    return this.http.get<AstaPlan>(`${environment.apiUrl}asta/plan?league_id=${leagueId}`);
  }

  /** Read a room's configuration. Never acts on it — see `app/CLAUDE.md`. */
  checkRoom(url: string): Observable<RoomCheck> {
    return this.http.get<RoomCheck>(`${environment.apiUrl}asta/room`, { params: { url } });
  }
}
