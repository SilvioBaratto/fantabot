import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AstaPlan } from '../models/asta-plan';
import { JournalPage } from '../models/journal';
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

  /**
   * One page of the CLI's room journal, newest first.
   *
   * Paged rather than whole: the recorded evening is 5,192 rows and 1.6 MB of JSON, and a
   * viewer that renders it in one response stalls the tab it opened in.
   */
  getJournal(offset: number, limit: number): Observable<JournalPage> {
    const params = new HttpParams().set('offset', offset).set('limit', limit);
    return this.http.get<JournalPage>(`${environment.apiUrl}asta/journal`, { params });
  }
}
