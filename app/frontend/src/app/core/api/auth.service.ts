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
}
