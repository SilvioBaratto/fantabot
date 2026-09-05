import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { Corpus, SeedPanel } from '../models/corpus';

@Injectable({ providedIn: 'root' })
export class HarvestService {
  private readonly http = inject(HttpClient);

  getCorpus(): Observable<Corpus> {
    return this.http.get<Corpus>(`${environment.apiUrl}harvest/corpus`);
  }

  /** The registry the next collect will follow — path, mtime, rows, per-format split. */
  getSeed(): Observable<SeedPanel> {
    return this.http.get<SeedPanel>(`${environment.apiUrl}harvest/seed`);
  }
}
