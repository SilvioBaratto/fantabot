import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { JobList, JobStatus } from '../models/job';

@Injectable({ providedIn: 'root' })
export class JobsService {
  private readonly http = inject(HttpClient);

  /**
   * One job. With `since`, only the lines from that index onward — a 1.5 s poll then
   * costs what happened since the last one instead of the whole log every time.
   */
  get(jobId: string, since?: number): Observable<JobStatus> {
    const options = since === undefined ? {} : { params: new HttpParams().set('since', since) };
    return this.http.get<JobStatus>(`${environment.apiUrl}jobs/${jobId}`, options);
  }

  /** What the server is running. The source of truth a page reattaches from. */
  list(): Observable<JobList> {
    return this.http.get<JobList>(`${environment.apiUrl}jobs`);
  }

  /** Ask a job to stop. 409 means it has no way to be stopped — not that it failed. */
  stop(jobId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(`${environment.apiUrl}jobs/${jobId}/stop`, {});
  }
}
