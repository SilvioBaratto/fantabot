import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { DbHealth } from '../models/db-health';

@Injectable({ providedIn: 'root' })
export class DbHealthService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}db/health`;

  getHealth(): Observable<DbHealth> {
    return this.http.get<DbHealth>(this.url);
  }
}
