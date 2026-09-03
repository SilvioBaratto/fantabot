import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { JobStatus } from '../models/job';

@Injectable({ providedIn: 'root' })
export class JobsService {
  private readonly http = inject(HttpClient);

  get(jobId: string): Observable<JobStatus> {
    return this.http.get<JobStatus>(`${environment.apiUrl}jobs/${jobId}`);
  }
}
