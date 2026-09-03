import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { SystemComponent } from './system';

describe('SystemComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SystemComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        ICON_PROVIDER,
        {
          provide: LucideIconConfig,
          useFactory: () => {
            const cfg = new LucideIconConfig();
            cfg.size = 16;
            return cfg;
          },
        },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('renders the connected status and table rows from /db/health', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges(); // ngOnInit fires the request

    const req = httpMock.expectOne(`${environment.apiUrl}db/health`);
    req.flush({
      ok: true,
      latency_ms: 12.3,
      error: null,
      tables: [{ name: 'quotazioni', exists: true, row_count: 571, size_pretty: '128 kB' }],
    });

    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Connected');
    expect(text).toContain('quotazioni');
    expect(text).toContain('571');
  });

  it('shows an error state when the API is unreachable', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).error(new ProgressEvent('error'));

    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('unreachable');
  });
});
