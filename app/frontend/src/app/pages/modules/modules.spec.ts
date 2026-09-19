import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { ModulesComponent } from './modules';

const GRID_URL = `${environment.apiUrl}asta/legality`;

describe('ModulesComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ModulesComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), ICON_PROVIDER],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  /** Renders, flushes the one request, and settles. */
  async function load(body: object): Promise<ComponentFixture<ModulesComponent>> {
    const fixture = TestBed.createComponent(ModulesComponent);
    fixture.detectChanges(); // ngOnInit fires the request
    httpMock.expectOne(GRID_URL).flush(body);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  it('renders a card per schema with its slots', async () => {
    const fixture = await load({
      schemi: [{ nome: '3-4-3', slots: [['Dc'], ['Dc', 'B']] }],
      roles: ['B', 'Dc'],
    });

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('3-4-3');
    expect(text).toContain('Dc/B');

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelectorAll('mat-card')).toHaveLength(1);
    // One list item per slot, in the order the grid declares them.
    const slots = Array.from(host.querySelectorAll('.slot')).map((el) => el.textContent?.trim());
    expect(slots).toEqual(['Dc', 'Dc/B']);
  });

  it('names the page once, and names each schema below it', async () => {
    const fixture = await load({
      schemi: [
        { nome: '3-4-3', slots: [['Dc']] },
        { nome: '4-4-2', slots: [['Ds']] },
      ],
      roles: ['Dc', 'Ds'],
    });

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelectorAll('h1')).toHaveLength(1);
    expect(host.querySelector('h1')?.textContent?.trim()).toBe('Mantra modules');
    // h2 per card: the next level down, never skipped.
    expect(Array.from(host.querySelectorAll('h2')).map((el) => el.textContent?.trim())).toEqual([
      '3-4-3',
      '4-4-2',
    ]);
  });

  it('gives both lists an accessible name, since the markers are styled away', async () => {
    const fixture = await load({
      schemi: [{ nome: '3-4-3', slots: [['Dc'], ['Dc', 'B']] }],
      roles: ['B', 'Dc'],
    });

    const host = fixture.nativeElement as HTMLElement;
    const feed = host.querySelector('.schema-feed');
    expect(feed?.getAttribute('role')).toBe('list');
    expect(feed?.getAttribute('aria-label')).toBe('Modules');

    const slots = host.querySelector('.slot-list');
    expect(slots?.getAttribute('role')).toBe('list');
    expect(slots?.getAttribute('aria-label')).toBe('Slots in 3-4-3');
  });

  it('shows a labelled progress bar while the grid is in flight', () => {
    const fixture = TestBed.createComponent(ModulesComponent);
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const bar = host.querySelector('mat-progress-bar');
    expect(bar).not.toBeNull();
    expect(bar?.getAttribute('aria-label')).toBe('Loading modules');
    expect(host.querySelector('.schema-feed')).toBeNull();

    httpMock.expectOne(GRID_URL).flush({ schemi: [], roles: [] });
  });

  it('announces an unreachable API instead of an empty page', async () => {
    const fixture = TestBed.createComponent(ModulesComponent);
    fixture.detectChanges();
    httpMock.expectOne(GRID_URL).error(new ProgressEvent('error'));
    fixture.detectChanges();
    await fixture.whenStable();

    const host = fixture.nativeElement as HTMLElement;
    const alert = host.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain('Could not reach the API.');
    expect(alert?.textContent).toContain('Make sure fantabot-app is running.');
    expect(host.querySelector('mat-progress-bar')).toBeNull();
  });

  it('explains a grid the API degraded to empty', async () => {
    // GET /asta/legality returns 200 with no schemi when mantra_schemi.json cannot be read.
    const fixture = await load({ schemi: [], roles: [] });

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('No modules found');
    expect(host.querySelector('.schema-feed')).toBeNull();
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });
});
