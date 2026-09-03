import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { DriftRow, NewsRow } from '../models/news';

@Injectable({ providedIn: 'root' })
export class NewsService {
  private readonly http = inject(HttpClient);

  getFeed(limit = 0): Observable<NewsRow[]> {
    const query = limit > 0 ? `?limit=${limit}` : '';
    return this.http.get<NewsRow[]>(`${environment.apiUrl}news${query}`);
  }

  getDrifted(): Observable<DriftRow[]> {
    return this.http.get<DriftRow[]>(`${environment.apiUrl}news/drifted`);
  }
}
