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

    httpMock.expectOne((r) => r.url.endsWith('jobs')).flush({
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

    httpMock.expectOne((r) => r.url.endsWith('jobs')).flush({
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
});
