import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { LegalityGrid } from '../models/legality';

@Injectable({ providedIn: 'root' })
export class LegalityService {
  private readonly http = inject(HttpClient);

  getGrid(): Observable<LegalityGrid> {
    return this.http.get<LegalityGrid>(`${environment.apiUrl}asta/legality`);
  }
}
