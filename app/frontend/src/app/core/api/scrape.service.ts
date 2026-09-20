import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { ScrapeRequest, ScrapeTables } from '../models/scrape';

/**
 * `fantabot db scrape`, over the wire.
 *
 * A job, unlike `TeamsService`'s two: three seasons of `voti` is ~114 polite GETs a
 * second apart, so it is a supervised child with a progress log and the page polls
 * `GET /jobs/{id}` for it.
 */
@Injectable({ providedIn: 'root' })
export class ScrapeService {
  private readonly http = inject(HttpClient);

  /** What may be scraped, in the order a fresh database needs them run. */
  tables(): Observable<ScrapeTables> {
    return this.http.get<ScrapeTables>(`${environment.apiUrl}db/scrape/tables`);
  }

  /** Start the child. The refusals are the command's own, so a bad season is a 400. */
  run(request: ScrapeRequest): Observable<{ job_id: string }> {
    return this.http.post<{ job_id: string }>(`${environment.apiUrl}db/scrape`, request);
  }
}
