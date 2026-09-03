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

  startLogin(league = 0): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}auth/login?league=${league}`,
      {},
    );
  }

  confirmLogin(jobId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(
      `${environment.apiUrl}auth/login/${jobId}/confirm`,
      {},
    );
  }
}
