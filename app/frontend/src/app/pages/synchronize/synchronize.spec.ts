import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { SynchronizeComponent } from './synchronize';

describe('SynchronizeComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SynchronizeComponent],
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

  it('starts a lega sync job for the entered league id', () => {
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith('jobs')).flush({ jobs: [] });

    fixture.componentInstance.setLeagueId('4103937');
    fixture.componentInstance.runLegaSync();

    httpMock
      .expectOne((r) => r.url.includes('actions/lega-sync') && r.url.includes('4103937'))
      .flush({ job_id: 'J1' });
    fixture.detectChanges();

    expect(fixture.componentInstance.running()).toBe(true);
    // poll() starts interval(1500); no timer advance -> no /jobs request. Destroy to cancel.
    fixture.destroy();
  });

  it('does not start when no league id is entered', () => {
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith('jobs')).flush({ jobs: [] });

    fixture.componentInstance.runLegaSync();

    httpMock.expectNone((r) => r.url.includes('actions/lega-sync'));
    expect(fixture.componentInstance.running()).toBe(false);
  });

  it('reattaches to a lega sync that is still running after a refresh', () => {
    // The running job id used to live only in component state, so a refresh mid-sync
    // orphaned the job invisibly *and* re-enabled the button that starts a second one.
    // GET /jobs is now the source of truth.
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.endsWith('jobs'))
      .flush({
        jobs: [
          {
            id: 'J9',
            kind: 'lega-sync',
            status: 'running',
            started_at: '2026-09-05T18:00:00+00:00',
            line_count: 3,
            ok: null,
            stoppable: false,
          },
        ],
      });
    fixture.detectChanges();

    expect(fixture.componentInstance.running()).toBe(true);
    fixture.destroy();
  });

  it('does not reattach to a job that has already finished', () => {
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.endsWith('jobs'))
      .flush({
        jobs: [
          {
            id: 'J8',
            kind: 'lega-sync',
            status: 'done',
            started_at: '2026-09-05T18:00:00+00:00',
            line_count: 3,
            ok: true,
            stoppable: false,
          },
        ],
      });
    fixture.detectChanges();

    expect(fixture.componentInstance.running()).toBe(false);
  });

  it('survives a jobs listing that cannot be read', () => {
    // A refresh while the API is down must leave a usable page, not a dead one.
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.endsWith('jobs'))
      .flush({ detail: 'nope' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(fixture.componentInstance.running()).toBe(false);
    expect(fixture.componentInstance.errorMsg()).toBeNull();
  });

  it('offers only the lega sync', () => {
    // News fetch moved off this page; the endpoint and ActionsService.runNewsFetch
    // both remain, for wherever it lands next.
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith('jobs')).flush({ jobs: [] });

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Lega sync');
    expect(text).not.toContain('News fetch');
    httpMock.expectNone((r) => r.url.includes('actions/news-fetch'));
  });

  /** Arrange a fixture that has already settled its reattach read. */
  function idlePage() {
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.endsWith('jobs')).flush({ jobs: [] });
    fixture.detectChanges();
    return fixture;
  }

  it('shows a placeholder until a sync has run', () => {
    // The outcome pane is the second pane from 840px up, so it needs something in it
    // before the first run rather than an empty box beside the control.
    const fixture = idlePage();

    expect(fixture.componentInstance.outcome()).toBe('idle');
    expect(fixture.nativeElement.querySelector('[data-run-status]')).toBeNull();
    expect(fixture.nativeElement.textContent as string).toContain('No sync has run yet');
  });

  it('keeps a partial sync distinct from a clean one and from a failure', () => {
    // `lega_sync.collect` fails PER READ: eight reads can end with six landed and two not,
    // and the job still returns with ok=false. Collapsing that into either neighbour is the
    // defect this asserts against — "Completed" would hide that a re-run is needed, and
    // "Failed" would claim nothing was written when six tables were.
    const fixture = idlePage();
    const page = fixture.componentInstance;

    page.jobStatus.set('done');
    page.jobOk.set(true);
    fixture.detectChanges();
    expect(page.outcome()).toBe('complete');
    expect(page.statusLabel()).toBe('Completed');

    page.jobOk.set(false);
    fixture.detectChanges();
    expect(page.outcome()).toBe('partial');
    expect(fixture.nativeElement.querySelector('[data-run-status="partial"]')).not.toBeNull();
    expect(fixture.nativeElement.textContent as string).toContain('Completed with failures');

    page.jobStatus.set('error');
    fixture.detectChanges();
    expect(page.outcome()).toBe('failed');
    expect(page.statusLabel()).toBe('Failed');
  });

  it('names the outcome rather than echoing the registry status', () => {
    const fixture = idlePage();
    const page = fixture.componentInstance;

    page.jobStatus.set('running');
    page.running.set(true);
    fixture.detectChanges();

    expect(page.outcome()).toBe('running');
    const status = fixture.nativeElement.querySelector('[data-run-status="running"]');
    expect(status).not.toBeNull();
    expect((status.textContent as string).trim()).toBe('Running');
  });

  it('gives every reported read its own row', () => {
    // Failure is per read, so the log is a list of outcomes and never one summary line.
    const fixture = idlePage();
    const page = fixture.componentInstance;

    page.jobStatus.set('running');
    page.lines.set(['8 squadre · 240 giocatori tesserati', '2 competizioni']);
    fixture.detectChanges();

    const rows = fixture.nativeElement.querySelectorAll('.run-log-line');
    expect(rows.length).toBe(2);
    expect((rows[1].textContent as string).trim()).toBe('2 competizioni');
  });

  it('keeps the status live region in the DOM before a run starts', () => {
    // A live region inserted at the same moment as its content is not announced; this one
    // has to exist from first paint for "Completed with failures" to be read out.
    const fixture = idlePage();

    expect(fixture.nativeElement.querySelector('[aria-live="polite"]')).not.toBeNull();
  });
  // ---------------------------------------------------------------------------------
  // T25 — the two one-shot team commands. Neither is a job: one small GET plus an
  // insert, and one SQL pass. So each answers in its own request and renders its own
  // outcome, never into the lega-sync run log, which belongs to a job that is running.
  // ---------------------------------------------------------------------------------

  it('captures a team snapshot for the league id already on the page', () => {
    // One field, not two. The page has a league id because lega sync needs one, and a
    // second box for the same number is a second thing to get wrong.
    const fixture = idlePage();
    fixture.componentInstance.setLeagueId('4103937');

    fixture.componentInstance.captureSnapshot();

    const request = httpMock.expectOne(`${environment.apiUrl}db/snapshot-team`);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ league_id: 4103937 });
    request.flush({
      outcome: 'saved',
      reason: '',
      league_id: 4103937,
      team_id: 1,
      nome: 'Legamiallerotaie',
      owner: 'silvio',
      credits_initial: 500,
      credits_spent: 474,
      credits_remaining: 26,
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.snapshot()?.outcome).toBe('saved');
    expect(fixture.componentInstance.snapshotting()).toBe(false);
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Legamiallerotaie');
    expect(text).toContain('474');
    expect(text).toContain('26');
  });

  it('does not capture a snapshot without a league id', () => {
    // `league_team_snapshot` is append-only, so a row written under a lega nobody picked
    // stays. The server requires the field; the button is the first place to refuse.
    const fixture = idlePage();

    fixture.componentInstance.captureSnapshot();

    httpMock.expectNone((r) => r.url.includes('db/snapshot-team'));
    expect(fixture.componentInstance.snapshot()).toBeNull();
  });

  it('shows the server sentence when a capture is refused', () => {
    // Verbatim, never composed here: the operator reading this on a page and the one
    // reading it in a terminal are the same person, and the remedy is in the wording.
    const fixture = idlePage();
    fixture.componentInstance.setLeagueId('4103937');

    fixture.componentInstance.captureSnapshot();
    httpMock.expectOne((r) => r.url.includes('db/snapshot-team')).flush({
      outcome: 'no_credential',
      reason: 'No encryption key set — connect an account first.',
      league_id: 4103937,
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.snapshot()?.outcome).toBe('no_credential');
    expect(fixture.nativeElement.textContent as string).toContain(
      'No encryption key set — connect an account first.',
    );
  });

  it('turns an unreachable API into the same shape a refusal has', () => {
    // A transport failure and a named outcome are one state on this control, so the page
    // has one branch to render rather than two that can disagree.
    const fixture = idlePage();
    fixture.componentInstance.setLeagueId('4103937');

    fixture.componentInstance.captureSnapshot();
    httpMock
      .expectOne((r) => r.url.includes('db/snapshot-team'))
      .flush({ detail: 'nope' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(fixture.componentInstance.snapshot()?.outcome).toBe('unreachable');
    expect(fixture.componentInstance.snapshotting()).toBe(false);
  });

  it('resolves club names and says how many changed', () => {
    const fixture = idlePage();

    fixture.componentInstance.resolveClubNames();

    const request = httpMock.expectOne(`${environment.apiUrl}db/backfill-teams`);
    expect(request.request.method).toBe('POST');
    request.flush({ outcome: 'resolved', reason: '', changed: 17 });
    fixture.detectChanges();

    expect(fixture.componentInstance.backfill()?.changed).toBe(17);
    expect(fixture.nativeElement.textContent as string).toContain('17');
  });

  it('takes no league id for a club-name backfill', () => {
    // The command has no `--league`: `teams` is Serie A's clubs, not a lega's. A page
    // that gated the button on the field would invent a dependency the CLI does not have.
    const fixture = idlePage();

    fixture.componentInstance.resolveClubNames();

    httpMock.expectOne(`${environment.apiUrl}db/backfill-teams`).flush({
      outcome: 'resolved',
      reason: '',
      changed: 0,
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.backfill()?.outcome).toBe('resolved');
  });

  it('keeps a mapping refusal apart from an outage, with its own remedy', () => {
    // `unresolved` means the mapping is untrustworthy and nothing was written — the
    // remedy is a scrape. `unreachable` means the database. Rendering them alike sends
    // the operator to start a server that is already running.
    const fixture = idlePage();

    fixture.componentInstance.resolveClubNames();
    httpMock.expectOne((r) => r.url.includes('db/backfill-teams')).flush({
      outcome: 'unresolved',
      reason: "club names not resolved (no name for code 'PIS') — nothing was written.",
      changed: 0,
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.backfill()?.outcome).toBe('unresolved');
    expect(fixture.nativeElement.textContent as string).toContain("no name for code 'PIS'");
  });

  it('never writes a one-shot result into the lega sync run log', () => {
    // The log is a job's, polled from `GET /jobs/{id}`. A request/response result
    // appended to it would read as a read that landed during a sync that never ran.
    const fixture = idlePage();
    fixture.componentInstance.setLeagueId('4103937');

    fixture.componentInstance.captureSnapshot();
    httpMock.expectOne((r) => r.url.includes('db/snapshot-team')).flush({
      outcome: 'saved',
      reason: '',
      league_id: 4103937,
      team_id: 1,
      nome: 'Legamiallerotaie',
      owner: 'silvio',
      credits_initial: 500,
      credits_spent: 474,
      credits_remaining: 26,
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.lines()).toEqual([]);
    expect(fixture.componentInstance.outcome()).toBe('idle');
  });
});
