import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { TargetPricesReport } from '../models/target-prices';

@Injectable({ providedIn: 'root' })
export class PricingService {
  private readonly http = inject(HttpClient);

  /**
   * Read the report. **Writes nothing** — `stored` comes back 0.
   *
   * This used to be the only call, and the endpoint behind it upserted `target_price`, so
   * opening this page mutated the database. A GET that writes is not a slow GET: it is a
   * page whose refresh is an action, on a route a browser is free to prefetch or retry.
   */
  getReport(system: string, topN = 15): Observable<TargetPricesReport> {
    return this.http.get<TargetPricesReport>(
      `${environment.apiUrl}asta/target-prices?system=${system}&top_n=${topN}`,
    );
  }

  /** Fit and store, the way `fantabot db price` does. The one call here that writes. */
  storeReport(system: string, topN = 15): Observable<TargetPricesReport> {
    return this.http.post<TargetPricesReport>(
      `${environment.apiUrl}asta/target-prices?system=${system}&top_n=${topN}`,
      {},
    );
  }
}
