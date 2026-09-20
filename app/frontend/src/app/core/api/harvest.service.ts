import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { BackfillCandidates, BackfillRequest } from '../models/backfill';
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

  /**
   * Carry the landing zone into Postgres, supervised as a child process.
   *
   * `astaType` is a parameter of the *read* — a seed holds both formats and a load
   * carries one of them — and not the collection-time filter the scan is forbidden.
   */
  startLoad(astaType: string, follow: boolean): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}harvest/load?asta_type=${encodeURIComponent(astaType)}&follow=${follow}`,
      {},
    );
  }

  /** What a backfill may be pointed at: the recorded logs and the seeds, by name. */
  getBackfillCandidates(): Observable<BackfillCandidates> {
    return this.http.get<BackfillCandidates>(`${environment.apiUrl}harvest/backfill/candidates`);
  }

  /**
   * Load a recorded collector log, supervised as a child process.
   *
   * A body of **names**, never paths, and the server refuses any name its own candidate
   * list does not hold — so a second landing zone cannot be created from this screen.
   */
  startBackfill(request: BackfillRequest): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(`${environment.apiUrl}harvest/backfill`, request);
  }

  /**
   * Subscribe to the live auctions in the seed and append every state to the landing zone.
   *
   * `pool` and nothing else: one seed carries both formats, and a selector here would be
   * the collection-time filter again.
   */
  startCollect(pool: number): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(
      `${environment.apiUrl}harvest/collect?pool=${pool}`,
      {},
    );
  }
}
