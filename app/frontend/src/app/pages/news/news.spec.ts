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

  /**
   * Material 3 conversion. These pin the properties the redesign is responsible for, not
   * its markup: one `h1`, a reading that survives losing colour, and a meter that says
   * nothing a screen reader has to hear.
   */
  describe('material 3 structure', () => {
    async function rendered() {
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
          {
            player_id: '3',
            nome: 'Immobile',
            sentiment: -0.9,
            disponibilita: 0.2,
            titolarita: 0.3,
            forma: 0.1,
            confidenza: 0.4,
            ruoli_mantra: 'Pc',
          },
        ],
        [],
      );
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture.nativeElement as HTMLElement;
    }

    it('has exactly one h1 and no skipped heading level', async () => {
      const host = await rendered();

      expect(host.querySelectorAll('h1').length).toBe(1);
      expect(host.querySelector('h1')?.textContent?.trim()).toBe('News');
      // h1 -> h2 (sections) -> h3 (one per reading card). Nothing jumps a level.
      expect(host.querySelectorAll('h2').length).toBeGreaterThan(0);
      expect(host.querySelectorAll('h3').length).toBe(2);
    });

    it('says the sentiment direction in text, so it never reads as colour alone', async () => {
      const host = await rendered();
      const labels = Array.from(host.querySelectorAll('.sr-only')).map((n) => n.textContent);

      expect(labels).toContain('Positive sentiment');
      expect(labels).toContain('Negative sentiment');
    });

    it('keeps the meters out of the a11y tree, because the number is already text', async () => {
      const host = await rendered();
      const meters = Array.from(host.querySelectorAll('.meter'));

      expect(meters.length).toBe(6); // three readings per card, two cards
      expect(meters.every((m) => m.getAttribute('aria-hidden') === 'true')).toBe(true);
      expect(host.textContent).toContain('0.80');
    });

    it('keeps the season control a labelled text input the specs can still find', async () => {
      const host = await rendered();
      const input = host.querySelector<HTMLInputElement>('input#news-season');

      expect(input).not.toBeNull();
      expect(input?.value).toBe('2026/27');
      expect(host.querySelector('mat-form-field mat-label')?.textContent?.trim()).toBe('Season');
    });

    it('carries no Tailwind utility class into the rendered DOM', async () => {
      const host = await rendered();
      // Matched per class token and anchored at its start, so Material's own namespaced
      // classes (`mat-…`, `mdc-…`, `cdk-…`) can never be mistaken for a utility: only a
      // token that *begins* `text-`, `w-`, `flex` and so on is a finding.
      const utility =
        /^(flex|grid|hidden|truncate|uppercase|relative|absolute|sticky|block|inline-flex)$|^(p|m|px|py|pt|pb|pl|pr|mx|my|mt|mb|gap|text|bg|border|rounded|w|h|min|max|space|font|items|justify|overflow|z|shadow|tracking|leading)-|^(sm|md|lg|xl|hover|focus|dark):/;
      const offenders = Array.from(host.querySelectorAll<HTMLElement>('[class]'))
        .flatMap((el) => Array.from(el.classList))
        .filter((token) => utility.test(token));

      expect(Array.from(new Set(offenders))).toEqual([]);
    });
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

    it('has its live region in the DOM before any fetch has run', async () => {
      // The region used to be created together with its first message — `@if (jobStatus())`
      // wrapped it — and a live region inserted with its content is generally not
      // announced, so "Running" was lost every time. `synchronize.html` is the pattern:
      // an unconditional slot with the `@if` inside it.
      const fixture = await ready();
      const host = fixture.nativeElement as HTMLElement;

      const live = host.querySelector('.job-status-slot');
      expect(live).not.toBeNull();
      expect(live?.getAttribute('aria-live')).toBe('polite');
      // Nothing to say yet, and genuinely `:empty` — that is what hides the strip, and a
      // stray whitespace node would leave an empty grey band on the card for ever.
      expect(live?.matches(':empty')).toBe(true);
      expect(host.querySelector('.job-status')).toBeNull();
    });

    it('announces the first status into the region that was already there', async () => {
      const fixture = await ready();
      fixture.componentInstance.runFetch();
      httpMock.expectOne((r) => r.url.includes('actions/news-fetch')).flush({ job_id: 'job-1' });
      fixture.detectChanges();
      await fixture.whenStable();

      const host = fixture.nativeElement as HTMLElement;
      const live = host.querySelector('.job-status-slot');
      expect(live?.textContent).toContain('Running');
      // The status itself must not be a region of its own, or the message would arrive
      // with its container again.
      expect(host.querySelector('.job-status')?.hasAttribute('aria-live')).toBe(false);
    });

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
      // The server's token is `running`; the screen writes it in sentence case. What is
      // being asserted is unchanged — that the page says a fetch is under way.
      expect(fixture.nativeElement.textContent).toContain('Running');
      expect(
        (fixture.nativeElement as HTMLElement).querySelector('[aria-live="polite"]'),
      ).not.toBeNull();
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
