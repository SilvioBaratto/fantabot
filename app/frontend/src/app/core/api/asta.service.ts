import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AstaPlan } from '../models/asta-plan';
import { JournalPage } from '../models/journal';
import { BidStarted, RoomCheck } from '../models/room';

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

  /**
   * The tail of the same file — the rows written after line `since`, oldest first.
   *
   * `since` rather than a reused `offset`, because they mean different things: `offset`
   * is how many rows to skip from the newest, `since` is a row's own 1-based line number.
   * A client that sent its tail position back as `offset` would page from the wrong end
   * and be told nothing was wrong.
   *
   * Oldest first is the server's doing and the reason this is a separate call at all: a
   * tail is appended to a list on a screen while the room is still running, and a viewer
   * that reversed each response before appending it would draw the evening inside out
   * every two seconds.
   */
  followJournal(since: number, limit: number): Observable<JournalPage> {
    const params = new HttpParams().set('follow', '1').set('since', since).set('limit', limit);
    return this.http.get<JournalPage>(`${environment.apiUrl}asta/journal`, { params });
  }

  /**
   * Watch a live room, supervised as a child process. It reads; it never bids.
   *
   * The link and nothing else. No `arm` — that is the lock the operator opens
   * deliberately, at the keyboard, and 3.9b is where the app learns to send it. And no
   * number from the value model: `--lam`, `--budget` and the three alphas are declared
   * once in `interface/asta.py`, and a copy here is the second value model that
   * `application/asta_planner.py` exists to prevent.
   */
  watchRoom(url: string): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(`${environment.apiUrl}asta/room/watch`, { url });
  }

  /**
   * Bid in a live room, supervised as a child process. **The one call here that can spend
   * credits — and it does not spend them: the child holds the locks.**
   *
   * `arm` is always sent and never defaulted. `application/arming`'s rule: a page can be
   * reloaded, restored by the session manager, or left open overnight, and none of those
   * may carry an arming decision forward — so the intent is restated on every request that
   * could act, and the server answers 422 to one that does not say.
   *
   * Still no number from the value model. `--lam`, `--budget` and the three alphas are the
   * child's own option set; what the route *does* add is the room's own shape — shard,
   * seat, format, teams, credits — which it reads from the platform rather than guessing,
   * because `asta bid` is unauthenticated and cannot.
   */
  bidRoom(url: string, arm: boolean): Observable<BidStarted> {
    return this.http.post<BidStarted>(`${environment.apiUrl}asta/room/bid`, { url, arm });
  }
}
