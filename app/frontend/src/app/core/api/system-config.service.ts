import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { SystemConfig } from '../models/system-config';

@Injectable({ providedIn: 'root' })
export class SystemConfigService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}system/config`;

  getConfig(): Observable<SystemConfig> {
    return this.http.get<SystemConfig>(this.url);
  }
}
