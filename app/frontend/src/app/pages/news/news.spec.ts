import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { NewsComponent } from './news';

describe('NewsComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NewsComponent],
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

  function flush(feed: object[], drifted: object[]): void {
    httpMock
      .expectOne((r) => r.url.startsWith(`${environment.apiUrl}news`) && !r.url.includes('drifted'))
      .flush(feed);
    httpMock.expectOne((r) => r.url.includes('news/drifted')).flush(drifted);
  }

  /** The reattach listing. Answered empty unless a test is about a job already running. */
  function noRunningJobs(jobs: object[] = []): void {
    httpMock.expectOne(`${environment.apiUrl}jobs`).flush({ jobs });
  }

  it('renders the feed and the drift list', async () => {
    const fixture = TestBed.createComponent(NewsComponent);
    fixture.detectChanges();
    noRunningJobs();
    flush(
      [
        {
          player_id: '2',
          nome: 'Zaccagni',
          sentiment: 0.9,
          disponibilita: 1,
          titolarita: 0.8,
          forma: 0.5,
          confidenza: 0.9,
          ruoli_mantra: 'W;T',
        },
      ],
      [{ player_id: '2', nome: 'Zaccagni', ruoli_mantra: 'C', ruolo_campo: 'W', deriva_ruolo: 2 }],
    );
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Zaccagni');
    expect(text).toContain('Role drift');
    expect(text).toContain('Sentiment');
  });

  it('shows an empty state when there is no news', async () => {
    const fixture = TestBed.createComponent(NewsComponent);
    fixture.detectChanges();
    noRunningJobs();
    flush([], []);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No news yet');
  });

  /**
   * News fetch. It was removed from Synchronize at the operator's request and the
   * endpoint deliberately kept; this is where it lives now — the topic's own page, and
   * therefore no new nav entry (§7 of the archived phase spec: nine tabs became ten, not
   * thirteen).
   */
  describe('news fetch', () => {
    async function ready() {
      const fixture = TestBed.createComponent(NewsComponent);
      fixture.detectChanges();
      noRunningJobs();
      flush([], []);
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('starts a fetch for the season shown and polls the job', async () => {
      const fixture = await ready();
      fixture.componentInstance.runFetch();

      const start = httpMock.expectOne((r) => r.url.includes('actions/news-fetch'));
      expect(start.request.method).toBe('POST');
      expect(start.request.url).toContain('2026%2F27');
      start.flush({ job_id: 'job-1' });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.running()).toBe(true);
      expect(fixture.nativeElement.textContent).toContain('running');
    });

    it('reattaches to a fetch that outlived the page', async () => {
      // A fetch is 523 players through the Agent SDK. A refresh mid-run must not
      // re-enable the button that would start a second one.
      const fixture = TestBed.createComponent(NewsComponent);
      fixture.detectChanges();
      noRunningJobs([
        {
          id: 'job-live',
          kind: 'news-fetch',
          status: 'running',
          started_at: '2026-09-05T19:00:00Z',
          line_count: 3,
          ok: null,
          stoppable: false,
        },
      ]);
      flush([], []);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.running()).toBe(true);
    });

    it('ignores a job of another kind', async () => {
      const fixture = TestBed.createComponent(NewsComponent);
      fixture.detectChanges();
      noRunningJobs([
        {
          id: 'job-other',
          kind: 'harvest-collect',
          status: 'running',
          started_at: '2026-09-05T19:00:00Z',
          line_count: 3,
          ok: null,
          stoppable: false,
        },
      ]);
      flush([], []);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.running()).toBe(false);
    });

    it('says so when the fetch could not be started', async () => {
      const fixture = await ready();
      fixture.componentInstance.runFetch();
      httpMock
        .expectOne((r) => r.url.includes('actions/news-fetch'))
        .flush({ detail: 'no encryption key' }, { status: 500, statusText: 'error' });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.running()).toBe(false);
      expect(fixture.nativeElement.textContent).toContain('no encryption key');
    });
  });
});
