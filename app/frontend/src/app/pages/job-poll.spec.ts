import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../environments/environment';
import { ICON_PROVIDER } from '../icons';
import { HarvestComponent } from './harvest/harvest';
import { NewsComponent } from './news/news';
import { SynchronizeComponent } from './synchronize/synchronize';

/**
 * The 1.5 s job poll, as each page actually runs it — pinned before it was lifted.
 *
 * **Why this file exists.** Three pages carried their own copy of the same loop
 * (`interval(1500)` -> `switchMap(jobs.get(id, since))` -> `takeWhile(running, true)` ->
 * `takeUntilDestroyed`), and nothing drove it past its first tick. Measured 2026-09-24
 * over the 348 tests then in the suite: **two** assertions anywhere name a `?since=`
 * request — `harvest.spec.ts:571` and `:596`, the backfill receipt — and both read
 * `since=0`. Timers were advanced elsewhere, 31 times in `asta.spec.ts` and twice in
 * `accounts.spec.ts`, but those drive *other* loops: the journal tail and
 * `pollUntilDone`, neither of which sends a `since` at all.
 *
 * So `since` never moved off zero under test, and the append, the terminal frame and the
 * stop were **unobserved**: a rewrite could have asked for the whole log on every tick
 * and re-rendered it from the top with the suite green. Four mutations proved that before
 * this file was written — pinning `since` at 0, replacing instead of appending, dropping
 * the `error` read, and reattaching to a `done` job — and the 348 caught none of them.
 *
 * What is pinned here is therefore not a rule but **the behaviour as found**, including
 * the part of it that is a defect: `synchronize` never reads the job's `error`, so a
 * crashed scrape child renders the generic "did not finish" and the server's
 * `"{ExcType}: {msg}"` is dropped on the floor. That is written down as an assertion and
 * not quietly fixed — a refactor that changed it would be a behaviour change smuggled in
 * under a collapse, and the two are the operator's to tell apart.
 *
 * Two ticks, never one. One tick proves the loop starts; the second is the only thing
 * that can see `since` move off zero, and moving off zero is the whole of the
 * bookkeeping.
 */

const RUNNING = { ok: null as boolean | null, error: null as string | null };

describe('the job poll, as every page runs it', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarvestComponent, NewsComponent, SynchronizeComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
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

  afterEach(() => {
    // Drain, never flush: a request the fixture's teardown has already cancelled throws
    // from inside `afterEach`, which leaves the TestBed configured and takes down every
    // later spec file with "Cannot configure the test module".
    httpMock.match(() => true);
    httpMock.verify();
  });

  // --- harvest: the richest copy, and the one the other two were modelled on ----------

  describe('harvest', () => {
    async function render() {
      const fixture = TestBed.createComponent(HarvestComponent);
      fixture.detectChanges();
      httpMock.match((r) => r.url.includes('harvest/corpus')).forEach((r) => r.flush({}));
      httpMock.match((r) => r.url.includes('harvest/seed')).forEach((r) => r.flush({}));
      httpMock
        .match((r) => r.url.includes('harvest/backfill/candidates'))
        .forEach((r) => r.flush({ home: '', exists: false, error: null, logs: [], seeds: [] }));
      httpMock.match((r) => r.url.endsWith('jobs')).forEach((r) => r.flush({ jobs: [] }));
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('asks for the whole log once, then only for what it has not shown', async () => {
      vi.useFakeTimers();
      try {
        const fixture = await render();
        const page = fixture.componentInstance;

        page.runLoad();
        httpMock
          .expectOne(`${environment.apiUrl}harvest/load?asta_type=mantra&follow=true`)
          .flush({ job_id: 'L1' });

        await vi.advanceTimersByTimeAsync(1600);
        httpMock
          .expectOne(`${environment.apiUrl}jobs/L1?since=0`)
          .flush({ id: 'L1', status: 'running', lines: ['one', 'two'], ...RUNNING });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.loaderLines()).toEqual(['one', 'two']);
        expect(page.loaderStatus()).toBe('running');
        expect(page.loaderRunning()).toBe(true);
        expect(page.loaderJobId()).toBe('L1');

        // The second tick is the one that can see `since` move. It appends; it does not
        // replace, which is what a reattach after a refresh depends on.
        await vi.advanceTimersByTimeAsync(1500);
        httpMock
          .expectOne(`${environment.apiUrl}jobs/L1?since=2`)
          .flush({ id: 'L1', status: 'running', lines: ['three'], ...RUNNING });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.loaderLines()).toEqual(['one', 'two', 'three']);
      } finally {
        vi.useRealTimers();
      }
    });

    it('lands the terminal frame, clears the id, and re-reads what the job moved', async () => {
      vi.useFakeTimers();
      try {
        const fixture = await render();
        const page = fixture.componentInstance;

        page.runLoad();
        httpMock
          .expectOne(`${environment.apiUrl}harvest/load?asta_type=mantra&follow=true`)
          .flush({ job_id: 'L2' });

        await vi.advanceTimersByTimeAsync(1600);
        httpMock.expectOne(`${environment.apiUrl}jobs/L2?since=0`).flush({
          id: 'L2',
          status: 'error',
          lines: ['exited 1'],
          ok: false,
          error: 'RuntimeError: the landing zone moved',
        });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.loaderStatus()).toBe('error');
        expect(page.loaderRunning()).toBe(false);
        expect(page.loaderOk()).toBe(false);
        // The server's own words, not a generic sentence.
        expect(page.loaderError()).toBe('RuntimeError: the landing zone moved');
        // Cleared, so the stop control has nothing left to address.
        expect(page.loaderJobId()).toBeNull();
        // `onFinish` — a load is the one job for which re-reading the corpus says
        // something true.
        expect(httpMock.match((r) => r.url.includes('harvest/corpus')).length).toBe(1);

        // And the loop is over: a terminal frame is the last request it makes.
        await vi.advanceTimersByTimeAsync(4600);
        expect(httpMock.match((r) => r.url.includes('jobs/L2')).length).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it('reattaches a running job and ignores a finished one of the same kind', async () => {
      const fixture = TestBed.createComponent(HarvestComponent);
      fixture.detectChanges();
      httpMock.match((r) => r.url.includes('harvest/corpus')).forEach((r) => r.flush({}));
      httpMock.match((r) => r.url.includes('harvest/seed')).forEach((r) => r.flush({}));
      httpMock
        .match((r) => r.url.includes('harvest/backfill/candidates'))
        .forEach((r) => r.flush({ home: '', exists: false, error: null, logs: [], seeds: [] }));
      httpMock
        .match((r) => r.url.endsWith('jobs'))
        .forEach((r) =>
          r.flush({
            jobs: [
              // `GET /jobs` lists everything this session has ever run. A `done` row read as
              // live re-enables nothing and disables the button that starts the next one.
              { id: 'OLD', kind: 'harvest-collect', status: 'done', ok: true },
              { id: 'L9', kind: 'harvest-load', status: 'running', ok: null },
            ],
          }),
        );
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.loaderRunning()).toBe(true);
      expect(fixture.componentInstance.loaderJobId()).toBe('L9');
      expect(fixture.componentInstance.collecting()).toBe(false);
    });

    it('says nothing when the listing itself cannot be read', async () => {
      // Nothing the operator asked for has failed, so a red banner on arrival would be
      // about the poll rather than about them.
      const fixture = TestBed.createComponent(HarvestComponent);
      fixture.detectChanges();
      httpMock.match((r) => r.url.includes('harvest/corpus')).forEach((r) => r.flush({}));
      httpMock.match((r) => r.url.includes('harvest/seed')).forEach((r) => r.flush({}));
      httpMock
        .match((r) => r.url.includes('harvest/backfill/candidates'))
        .forEach((r) => r.flush({ home: '', exists: false, error: null, logs: [], seeds: [] }));
      httpMock
        .match((r) => r.url.endsWith('jobs'))
        .forEach((r) => r.flush('down', { status: 503, statusText: 'Service Unavailable' }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.loaderRunning()).toBe(false);
      expect(fixture.componentInstance.errorMsg()).toBeNull();
    });
  });

  // --- synchronize: the copy that drifted --------------------------------------------

  describe('synchronize', () => {
    async function render() {
      const fixture = TestBed.createComponent(SynchronizeComponent);
      fixture.detectChanges();
      httpMock
        .match((r) => r.url.includes('db/scrape/tables'))
        .forEach((r) =>
          r.flush({
            current_season: '2026/27',
            tables: [
              {
                table: 'voti',
                writes: ['voti'],
                requires: [],
                default_seasons: ['2025/26'],
                default_is_stale: true,
              },
            ],
          }),
        );
      httpMock.match((r) => r.url.endsWith('/lega')).forEach((r) => r.flush({}));
      httpMock.match((r) => r.url.endsWith('jobs')).forEach((r) => r.flush({ jobs: [] }));
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('appends off `since`, exactly as harvest does', async () => {
      vi.useFakeTimers();
      try {
        const fixture = await render();
        const page = fixture.componentInstance;
        page.setScrapeTable('voti');
        page.setScrapeSeasons(['2026/27']);

        page.runScrape();
        httpMock.expectOne(`${environment.apiUrl}db/scrape`).flush({ job_id: 'S1' });

        await vi.advanceTimersByTimeAsync(1600);
        httpMock
          .expectOne(`${environment.apiUrl}jobs/S1?since=0`)
          .flush({ id: 'S1', status: 'running', lines: ['giornata 1'], ...RUNNING });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.scrapeLines()).toEqual(['giornata 1']);

        await vi.advanceTimersByTimeAsync(1500);
        httpMock
          .expectOne(`${environment.apiUrl}jobs/S1?since=1`)
          .flush({ id: 'S1', status: 'running', lines: ['giornata 2'], ...RUNNING });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.scrapeLines()).toEqual(['giornata 1', 'giornata 2']);
      } finally {
        vi.useRealTimers();
      }
    });

    it('drops the crashed child’s own words, and keeps the job id', async () => {
      // **Pinned as found, and it is a defect.** `harvest` renders `job.error`; this copy
      // has no `error` on its panel and never reads the field, so the operator gets the
      // generic sentence and the `"{ExcType}: {msg}"` the server took the trouble to
      // carry is thrown away. The id is likewise left standing where harvest clears it —
      // unobservable today only because the stop control is inside `@if (scrapeRunning())`.
      vi.useFakeTimers();
      try {
        const fixture = await render();
        const page = fixture.componentInstance;
        page.setScrapeTable('voti');
        page.setScrapeSeasons(['2026/27']);

        page.runScrape();
        httpMock.expectOne(`${environment.apiUrl}db/scrape`).flush({ job_id: 'S2' });

        await vi.advanceTimersByTimeAsync(1600);
        httpMock.expectOne(`${environment.apiUrl}jobs/S2?since=0`).flush({
          id: 'S2',
          status: 'error',
          lines: ['exited 1'],
          ok: false,
          error: 'ConnectError: fantacalcio.it refused the connection',
        });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.scrapeStatus()).toBe('error');
        expect(page.scrapeRunning()).toBe(false);
        expect(page.scrapeOk()).toBe(false);
        expect(page.scrapeError()).toBeNull();
        expect(page.scrapeJobId()).toBe('S2');
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // --- news: the same loop over component signals rather than a panel -----------------

  describe('news', () => {
    async function render() {
      const fixture = TestBed.createComponent(NewsComponent);
      fixture.detectChanges();
      httpMock.match((r) => r.url.endsWith('jobs')).forEach((r) => r.flush({ jobs: [] }));
      httpMock.match((r) => r.url.includes('news/drifted')).forEach((r) => r.flush([]));
      httpMock
        .match((r) => r.url.includes('news') && !r.url.includes('drifted'))
        .forEach((r) => r.flush([]));
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('appends off `since` and re-reads the feed the fetch wrote', async () => {
      vi.useFakeTimers();
      try {
        const fixture = await render();
        const page = fixture.componentInstance;

        page.runFetch();
        httpMock.expectOne((r) => r.url.includes('actions/news-fetch')).flush({ job_id: 'N1' });

        await vi.advanceTimersByTimeAsync(1600);
        httpMock
          .expectOne(`${environment.apiUrl}jobs/N1?since=0`)
          .flush({ id: 'N1', status: 'running', lines: ['1/523'], ...RUNNING });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.lines()).toEqual(['1/523']);

        await vi.advanceTimersByTimeAsync(1500);
        httpMock.expectOne(`${environment.apiUrl}jobs/N1?since=1`).flush({
          id: 'N1',
          status: 'error',
          lines: ['2/523'],
          ok: false,
          error: 'AgentError: the model refused',
        });
        await vi.advanceTimersByTimeAsync(0);

        expect(page.lines()).toEqual(['1/523', '2/523']);
        expect(page.jobStatus()).toBe('error');
        expect(page.running()).toBe(false);
        expect(page.jobOk()).toBe(false);
        expect(page.jobError()).toBe('AgentError: the model refused');
        // The fetch is what puts rows on this screen, so it re-reads them.
        expect(httpMock.match((r) => r.url.includes('drifted')).length).toBe(1);
      } finally {
        vi.useRealTimers();
      }
    });

    it('reattaches a running fetch and ignores a finished one', async () => {
      // The id it polls is the assertion, not the flag: this page sets `running` for
      // whatever it found, so a predicate that stopped reading `status` would tail the
      // *finished* job with every flag on the screen still saying "running".
      vi.useFakeTimers();
      try {
        const fixture = TestBed.createComponent(NewsComponent);
        fixture.detectChanges();
        httpMock
          .match((r) => r.url.endsWith('jobs'))
          .forEach((r) =>
            r.flush({
              jobs: [
                { id: 'OLD', kind: 'news-fetch', status: 'done', ok: true },
                { id: 'N9', kind: 'news-fetch', status: 'running', ok: null },
              ],
            }),
          );
        httpMock.match((r) => r.url.includes('news/drifted')).forEach((r) => r.flush([]));
        httpMock
          .match((r) => r.url.includes('news') && !r.url.includes('drifted'))
          .forEach((r) => r.flush([]));
        fixture.detectChanges();
        await fixture.whenStable();

        expect(fixture.componentInstance.running()).toBe(true);
        expect(fixture.componentInstance.jobStatus()).toBe('running');

        await vi.advanceTimersByTimeAsync(1600);
        httpMock
          .expectOne(`${environment.apiUrl}jobs/N9?since=0`)
          .flush({ id: 'N9', status: 'running', lines: [], ...RUNNING });
        await vi.advanceTimersByTimeAsync(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it('says nothing when the listing itself cannot be read', async () => {
      const fixture = TestBed.createComponent(NewsComponent);
      fixture.detectChanges();
      httpMock
        .match((r) => r.url.endsWith('jobs'))
        .forEach((r) => r.flush('down', { status: 503, statusText: 'Service Unavailable' }));
      httpMock.match((r) => r.url.includes('news/drifted')).forEach((r) => r.flush([]));
      httpMock
        .match((r) => r.url.includes('news') && !r.url.includes('drifted'))
        .forEach((r) => r.flush([]));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.running()).toBe(false);
      expect(fixture.componentInstance.errorMsg()).toBeNull();
    });
  });
});
