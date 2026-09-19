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

    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    const levels = [...host.querySelectorAll('h1, h2, h3, h4, h5, h6')].map((e) =>
      Number(e.tagName.slice(1)),
    );

    expect(levels.filter((l) => l === 1)).toHaveLength(1);
    expect(host.querySelector('h1')?.textContent?.trim()).toBe('System');
    expect(new Set(levels)).toEqual(new Set([1, 2]));
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

    fixture.detectChanges();
    await fixture.whenStable();

    const refresh = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(refresh.textContent).toContain('Refresh');

    refresh.click();
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}db/health`)
      .flush({ ok: true, latency_ms: 5, error: null, tables: [] });

    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('shows an error state when the API is unreachable', async () => {
    const fixture = TestBed.createComponent(SystemComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}db/health`).error(new ProgressEvent('error'));

    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('unreachable');
    // A persistent, non-blocking status region — M3's banner, which Material has no
    // component for.
    expect(fixture.nativeElement.querySelector('[role="status"]')).toBeTruthy();
  });
});
