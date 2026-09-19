import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { SystemConfig } from '../../core/models/system-config';
import { ICON_PROVIDER } from '../../icons';
import { SystemComponent } from './system';

/** What `GET /system/config` answers. Secrets are booleans; the DSN is already masked. */
const CONFIG: SystemConfig = {
  settings: { fantabot_data_dir: 'data', fantabot_agent_base_url: '', cors_origins: [] },
  secrets_set: { fantabot_encryption_key: true, lega_password: false },
  database_url: 'postgresql+psycopg2://postgres:@/fantabot?host=/Users/me/.fantabot/pgdata',
  database_url_error: null,
};

/**
 * The page's *second* request, answered in every test.
 *
 * Not boilerplate: `httpMock.verify()` fails on an outstanding request, and a failing
 * `afterEach` leaves the TestBed instantiated — which broke `prices.spec.ts`,
 * `app.spec.ts` and `toast.spec.ts` in the same run, none of which touch this page.
 * A helper rather than a line per test so the two requests cannot drift apart.
 */
function flushConfig(mock: HttpTestingController, body: SystemConfig | 'error' = CONFIG): void {
  const req = mock.expectOne(`${environment.apiUrl}system/config`);
  if (body === 'error') {
    req.error(new ProgressEvent('error'));
  } else {
    req.flush(body);
  }
}

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
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Connected');
    expect(text).toContain('quotazioni');
    expect(text).toContain('571');
  });

  it('pairs every table with its row count in a definition list', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).flush({
      ok: true,
      latency_ms: 12.3,
      error: null,
      tables: [
        { name: 'quotazioni', exists: true, row_count: 571, size_pretty: '128 kB' },
        { name: 'statistiche', exists: false, row_count: null, size_pretty: '0 bytes' },
      ],
    });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    const inventory = host.querySelector('[aria-labelledby="tables-heading"]') as HTMLElement;
    const names = [...inventory.querySelectorAll('dt')].map((e) => e.textContent?.trim());
    const counts = [...inventory.querySelectorAll('dd')].map((e) => e.textContent?.trim());

    expect(names).toEqual(['quotazioni', 'statistiche']);
    expect(counts[0]).toBe('571');
    // A table the database has not got: the dash is decorative, the words carry it.
    expect(counts[1]).toContain('—');
    expect(inventory.querySelector('dd .sr-only')?.textContent).toBe('Not created');
  });

  it('shows the status as an icon and a word, not colour alone', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).flush({
      ok: false,
      latency_ms: 0,
      error: 'down',
      tables: [],
    });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const status = fixture.nativeElement.querySelector('.status') as HTMLElement;
    expect(status.textContent).toContain('Offline');
    expect(status.querySelector('lucide-icon')).toBeTruthy();
  });

  it('has exactly one h1 and no skipped heading level', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).flush({
      ok: true,
      latency_ms: 4,
      error: null,
      tables: [{ name: 'quotazioni', exists: true, row_count: 571, size_pretty: '128 kB' }],
    });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    const levels = [...host.querySelectorAll('h1, h2, h3, h4, h5, h6')].map((e) =>
      Number(e.tagName.slice(1)),
    );

    expect(levels.filter((l) => l === 1)).toHaveLength(1);
    expect(host.querySelector('h1')?.textContent?.trim()).toBe('System');
    // h3 is the Configuration section's cards under its own h2 — a level added, not a
    // level skipped, which is the property this test is actually about.
    expect(new Set(levels)).toEqual(new Set([1, 2, 3]));
  });

  it('shows an indeterminate progress bar only while the check is in flight', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    const bar = fixture.nativeElement.querySelector('mat-progress-bar') as HTMLElement | null;
    expect(bar).toBeTruthy();
    expect(bar?.getAttribute('mode')).toBe('indeterminate');

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeNull();
  });

  it('checks the database again when Refresh is pressed', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const refresh = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(refresh.textContent).toContain('Refresh');

    refresh.click();
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 5, error: null, tables: [] });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('shows an error state when the API is unreachable', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).error(new ProgressEvent('error'));
    flushConfig(httpMock, 'error');

    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('unreachable');
    // A persistent, non-blocking status region — M3's banner, which Material has no
    // component for.
    expect(fixture.nativeElement.querySelector('[role="status"]')).toBeTruthy();
  });

  // ------------------------------------------------------------------ T28: configuration

  it('shows the masked DSN, which is the answer the page exists to give', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const dsn = fixture.nativeElement.querySelector('.config-dsn') as HTMLElement;
    // Whole and unbroken: this is the string an operator pastes into alembic.ini, and
    // the CLI half of this fix exists because Rich was splitting it mid-token.
    expect(dsn.textContent?.trim()).toBe(CONFIG.database_url);
  });

  it('reports a secret as set or not set, never as a value', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    // Queried, not read off `textContent`. The first version asserted `toContain('set')`
    // and `toContain('not set')` on the whole page — which the section's own explanatory
    // line ("reported as set or not set, never shown") satisfies on its own, so blanking
    // every row left it green. Caught by mutation.
    const host = fixture.nativeElement as HTMLElement;
    const rows = [...host.querySelectorAll('.config-row')].filter((row) =>
      row.querySelector('.secret-state'),
    );
    const reported = new Map(
      rows.map((row) => [
        row.querySelector('.config-key')?.textContent?.trim(),
        row.querySelector('.secret-state')?.textContent?.trim(),
      ]),
    );

    expect(reported.get('fantabot_encryption_key')).toBe('set');
    expect(reported.get('lega_password')).toBe('not set');
  });

  it('renders a setting whose value is false or empty rather than leaving a blank cell', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock, {
      ...CONFIG,
      settings: { fantabot_auto_act: false, stats_source_base_url: '', api_port: 0 },
    });

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    const pane = host.querySelector('[aria-labelledby="config-heading"]') as HTMLElement;
    const values = [...pane.querySelectorAll('.config-value-mono')].map((e) =>
      e.textContent?.trim(),
    );

    // An empty cell where a setting reads `false` is the same screen as a setting that is
    // missing, and telling those apart is the whole job of this page.
    expect(values).toContain('false');
    expect(values).toContain('0');
    expect(values).toContain('(empty)');
  });

  it('sorts the settings, because model_dump returns declaration order', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock, {
      ...CONFIG,
      settings: { zeta: 1, alpha: 2, mid: 3 },
    });

    fixture.detectChanges();
    await fixture.whenStable();

    const pane = fixture.nativeElement.querySelector(
      '[aria-labelledby="config-heading"]',
    ) as HTMLElement;
    const keys = [...pane.querySelectorAll('.config-key')].map((e) => e.textContent?.trim());

    expect(keys.filter((k) => ['alpha', 'mid', 'zeta'].includes(k ?? ''))).toEqual([
      'alpha',
      'mid',
      'zeta',
    ]);
  });

  it('names an unparseable DSN and still shows every other setting', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock, {
      ...CONFIG,
      database_url: '',
      database_url_error: "Could not parse SQLAlchemy URL from string '::nope::'",
    });

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.config-error')?.textContent).toContain('Could not parse');
    expect(host.querySelector('.config-dsn')).toBeNull();
    // Degrade open: one unparseable field must not cost the operator the other settings.
    expect(host.textContent).toContain('fantabot_data_dir');
  });

  it('keeps the database panel when the config read fails on its own', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock, 'error');

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    // Two requests, two failures. The health probe is what reports a database that will
    // not open, so a config read that fails must not take it down with it.
    expect(host.textContent).toContain('Connected');
    expect(host.querySelector('[aria-labelledby="config-heading"]')).toBeNull();
  });
});
