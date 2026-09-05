import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AuthStatus } from '../models/auth-status';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}auth/status`;

  getStatus(): Observable<AuthStatus> {
    return this.http.get<AuthStatus>(this.url);
  }

  /**
   * Start the headed login.
   *
   * `force` matters after a disconnect: the backend skips the browser entirely when
   * every *remaining* stored token is still valid, so re-connecting one lega while
   * another is stored is a no-op without it.
   */
  startLogin(league = 0, force = false): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}auth/login?league=${league}&force=${force}`,
      {},
    );
  }

  /**
   * Start the headed FantaLab login.
   *
   * `force` is not optional in practice once a session exists: the backend refuses to
   * open a browser while any session is stored, so replacing one is impossible without
   * it. The confirm and job-polling endpoints are shared with the lega login.
   */
  startFantalabLogin(force = false): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}auth/fantalab-login?force=${force}`,
      {},
    );
  }

  /** Release a login job waiting on its confirm gate — "I have signed in". */
  confirmLogin(jobId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(
      `${environment.apiUrl}auth/login/${jobId}/confirm`,
      {},
    );
  }

  /** Remove one lega's stored token. `removed` is false when there was no row. */
  forgetLeague(leagueId: number): Observable<{ ok: boolean; removed: boolean }> {
    return this.http.delete<{ ok: boolean; removed: boolean }>(
      `${environment.apiUrl}auth/league/${leagueId}`,
    );
  }

  /** Remove one FantaLab session. */
  forgetFantalab(userId: string): Observable<{ ok: boolean; removed: boolean }> {
    return this.http.delete<{ ok: boolean; removed: boolean }>(
      `${environment.apiUrl}auth/fantalab/${encodeURIComponent(userId)}`,
    );
  }
}
