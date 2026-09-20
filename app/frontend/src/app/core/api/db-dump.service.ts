import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { DumpStarted, DumpTarget } from '../models/db-dump';

/**
 * `fantabot db dump`, over the wire.
 *
 * A job, like `ScrapeService` and unlike `TeamsService`: the database is ~1.9 GB and
 * `pg_dump` streams all of it, so the page starts a supervised child and polls.
 *
 * There is no `download()` here and there must never be one — `SPEC.md` §8 Never #4.
 * What the page offers is the path.
 */
@Injectable({ providedIn: 'root' })
export class DbDumpService {
  private readonly http = inject(HttpClient);

  /** Where the dump would land, asked before committing minutes to finding out. */
  target(): Observable<DumpTarget> {
    return this.http.get<DumpTarget>(`${environment.apiUrl}db/dump/target`);
  }

  /** Start the child. A `$HOME` that cannot hold a dump comes back `refused`, unspawned. */
  run(): Observable<DumpStarted> {
    return this.http.post<DumpStarted>(`${environment.apiUrl}db/dump`, {});
  }
}
