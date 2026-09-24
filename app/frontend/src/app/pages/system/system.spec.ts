import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { DumpTarget } from '../../core/models/db-dump';
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
 * `app.spec.ts` and the since-deleted `toast.spec.ts` in the same run, none of which
 * touch this page.
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

/** What `GET /db/dump/target` answers when `$HOME` can hold a dump and none is there yet. */
const TARGET: DumpTarget = {
  path: '/Users/me/fantabot-db-20260920.dump',
  refused: '',
  exists: false,
  size_bytes: null,
};

/**
 * The page's *third* request, answered in every test — `flushConfig`'s reason exactly.
 *
 * Every one of these is an `ngOnInit` side-request, and an unanswered one fails
 * `httpMock.verify()` in an `afterEach`, which leaves the TestBed instantiated and takes
 * unrelated spec files down with it.
 */
function flushDumpTarget(mock: HttpTestingController, body: DumpTarget = TARGET): void {
  mock.expectOne(`${environment.apiUrl}db/dump/target`).flush(body);
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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('shows an error state when the API is unreachable', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).error(new ProgressEvent('error'));
    flushConfig(httpMock, 'error');
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

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
    flushDumpTarget(httpMock);

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    // Two requests, two failures. The health probe is what reports a database that will
    // not open, so a config read that fails must not take it down with it.
    expect(host.textContent).toContain('Connected');
    expect(host.querySelector('[aria-labelledby="config-heading"]')).toBeNull();
  });

  // -- the dump card ---------------------------------------------------------------
  //
  // The archived parity-phase spec, §8 Never #4: no browser download of a database dump. The card names the
  // path and stops there, which is the whole of its job — the file carries the
  // `league_tokens` rows, and a download puts it wherever the browser puts downloads.

  /** Bring the page up with the health and config requests answered, for the dump tests. */
  async function pageWith(target: DumpTarget) {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 4, error: null, tables: [] });
    flushConfig(httpMock);
    flushDumpTarget(httpMock, target);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  it('names the path the dump would take, before anything is run', async () => {
    const fixture = await pageWith(TARGET);

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.textContent).toContain('/Users/me/fantabot-db-20260920.dump');
  });

  it('says that no dump has been taken today', async () => {
    const fixture = await pageWith(TARGET);

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.textContent).toContain('No backup yet today');
  });

  it('reports a dump already taken today, and how big it is', async () => {
    // One file per day, so a second run overwrites the first. The screen has to say what
    // is being replaced before the operator spends minutes replacing it.
    const fixture = await pageWith({ ...TARGET, exists: true, size_bytes: 412_000_000 });

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.textContent).toContain('393 MB');
  });

  it('a home that cannot hold a dump names the reason and offers no button', async () => {
    // Structural rather than transient: there is no path, so a greyed-out one beside a
    // path would be a screen claiming a file it will never write.
    const refusal = 'refusing to write the dump onto an external volume: /Volumes/x/f.dump';
    const fixture = await pageWith({ path: '', refused: refusal, exists: false, size_bytes: null });

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.textContent).toContain('/Volumes/x/f.dump');
    expect(pane.querySelector('.dump-button')).toBeNull();
  });

  it('starting a dump posts, and the button says it is running', async () => {
    const fixture = await pageWith(TARGET);

    (fixture.nativeElement.querySelector('.dump-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/dump`)
      .flush({ outcome: 'started', path: TARGET.path, job_id: 'j1', detail: '' });
    fixture.detectChanges();
    await fixture.whenStable();

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.textContent).toContain('Backing up');
    // The poll is in flight; answering it is what `httpMock.verify()` is owed.
    httpMock
      .expectOne(`${environment.apiUrl}jobs/j1`)
      .flush({ id: 'j1', status: 'running', lines: [], ok: null, error: null });
  });

  it('a finished dump re-reads the target, so the file that landed is on the screen', async () => {
    // The only evidence the dump worked that the page can show: `pg_dump` exits 0 against
    // an empty database too, and a 0-byte dump is the one worth panicking about.
    const fixture = await pageWith(TARGET);

    (fixture.nativeElement.querySelector('.dump-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}db/dump`)
      .flush({ outcome: 'started', path: TARGET.path, job_id: 'j1', detail: '' });
    httpMock
      .expectOne(`${environment.apiUrl}jobs/j1`)
      .flush({ id: 'j1', status: 'done', lines: ['exited 0'], ok: true, error: null });
    flushDumpTarget(httpMock, { ...TARGET, exists: true, size_bytes: 412_000_000 });
    fixture.detectChanges();
    await fixture.whenStable();

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.textContent).toContain('393 MB');
  });

  it('a dump that failed says so rather than leaving the button idle', async () => {
    const fixture = await pageWith(TARGET);

    (fixture.nativeElement.querySelector('.dump-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}db/dump`)
      .flush({ outcome: 'started', path: TARGET.path, job_id: 'j1', detail: '' });
    httpMock.expectOne(`${environment.apiUrl}jobs/j1`).flush({
      id: 'j1',
      status: 'done',
      lines: ['pg_dump exited 1 — is the database running?'],
      ok: false,
      error: null,
    });
    flushDumpTarget(httpMock);
    fixture.detectChanges();
    await fixture.whenStable();

    const pane = fixture.nativeElement.querySelector('[aria-labelledby="dump-heading"]');
    expect(pane.querySelector('.dump-error')?.textContent).toContain('pg_dump exited 1');
  });

  it('never offers the bytes', async () => {
    // The archived parity-phase spec, §8 Never #4, on the surface that would have to break it. Asserted against
    // the DOM rather than the component: what the rule forbids is a link on the screen.
    const fixture = await pageWith({ ...TARGET, exists: true, size_bytes: 412_000_000 });

    const pane = fixture.nativeElement.querySelector(
      '[aria-labelledby="dump-heading"]',
    ) as HTMLElement;
    expect(pane.querySelector('a[download]')).toBeNull();
    expect(pane.querySelector('a[href]')).toBeNull();
  });

  it('refuses to start a dump for a refused target, asked directly', async () => {
    // The template renders no button in this state, so the guard is only reachable from
    // the component — which is where a future template would reach it from. Without this,
    // `startDump`'s own check is code no test can fail.
    const refusal = 'refusing to write the dump onto an external volume: /Volumes/x/f.dump';
    const fixture = await pageWith({ path: '', refused: refusal, exists: false, size_bytes: null });

    fixture.componentInstance.startDump();

    httpMock.expectNone(`${environment.apiUrl}db/dump`);
    expect(fixture.componentInstance.dumping()).toBe(false);
  });
});
