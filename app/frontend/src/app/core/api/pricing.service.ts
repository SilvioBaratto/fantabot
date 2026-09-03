import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { TargetPricesReport } from '../models/target-prices';

@Injectable({ providedIn: 'root' })
export class PricingService {
  private readonly http = inject(HttpClient);

  getReport(system: string, topN = 15): Observable<TargetPricesReport> {
    return this.http.get<TargetPricesReport>(
      `${environment.apiUrl}asta/target-prices?system=${system}&top_n=${topN}`,
    );
  }
}
