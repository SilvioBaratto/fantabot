import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { Corpus } from '../../core/models/corpus';
import { ICON_PROVIDER } from '../../icons';
import { HarvestComponent } from './harvest';

/**
 * The corpus panel is the instrument every later collection increment is graded on
 * (`todo/TODO.md` §2). What these tests hold it to is not that it renders numbers —
 * it is that the three things §1.1 cost a week are all readable off it: a format that
 * collected nothing, the gap between registered and followed, and the filter the
 * headline number survived.
 */
describe('HarvestComponent', () => {
  let httpMock: HttpTestingController;

  const CORPUS: Corpus = {
    ok: true,
    num_credits: 500,
    num_teams: 8,
    error: null,
    formats: [
      {
        asta_type: 'classic',
        rooms: 4386,
        rooms_with_events: 1186,
        events: 2145179,
        assignments: 148720,
        assignments_with_buyer: 131870,
        assignments_with_player: 144815,
        planner_sales: 32100,
      },
      {
        asta_type: 'mantra',
        rooms: 0,
        rooms_with_events: 0,
        events: 0,
        assignments: 0,
        assignments_with_buyer: 0,
        assignments_with_player: 0,
        planner_sales: 0,
      },
    ],
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarvestComponent],
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

  async function render(body: Corpus = CORPUS) {
    const fixture = TestBed.createComponent(HarvestComponent);
    fixture.detectChanges(); // ngOnInit fires the request
    httpMock.expectOne(`${environment.apiUrl}harvest/corpus`).flush(body);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  it('renders every count for a format that has one', async () => {
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('classic');
    expect(text).toContain('2,145,179'); // events
    expect(text).toContain('1,186'); // rooms with events
    expect(text).toContain('32,100'); // through the planner's filter
  });

  it('renders a format that has collected nothing as zeroes rather than dropping it', async () => {
    // The case the panel exists for. A missing row reads as "no data yet"; a row of
    // zeroes reads as "this format is empty", which is the finding.
    const fixture = await render();
    const text = fixture.nativeElement.textContent as string;

    expect(text).toContain('mantra');
    expect(fixture.nativeElement.querySelectorAll('[data-format]').length).toBe(2);
  });

  it('states the filter the headline number survived, on screen', async () => {
    // 32,100 is meaningless without `8 x 500, buyer and player link present`, and a
    // tooltip is not the contract — `read_plan_inputs` records why the shape is not a law.
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('500');
    expect(text).toContain('8');
    expect(text.toLowerCase()).toContain('buyer');
  });

  it('shows a reason, not an empty table, when the read fails', async () => {
    const fixture = TestBed.createComponent(HarvestComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}harvest/corpus`).error(new ProgressEvent('error'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('unreachable');
  });

  it('shows the database error the endpoint degraded open with', async () => {
    // `ok: false` is not the same as an unreachable API: the endpoint answered, and what
    // it answered is that it could not read. Rendering that as an empty table would hide
    // the one line that says why.
    const fixture = await render({ ...CORPUS, ok: false, formats: [], error: 'OperationalError' });

    expect(fixture.nativeElement.textContent).toContain('OperationalError');
  });
});
