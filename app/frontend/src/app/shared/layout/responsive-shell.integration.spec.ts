import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { LucideIconConfig } from 'lucide-angular';

import { ICON_PROVIDER } from '../../icons';
import { routes } from '../../app.routes';
import { WINDOW_SIZE_QUERIES, WindowSizeClass } from '../../core/window-size-class';
import { LayoutComponent } from './layout';

/**
 * Cross-component integration: which navigation system the shell renders at each M3
 * window size class, and that the two never overlap.
 *
 * jsdom evaluates no CSS and reports no width, so the size class is driven the only way
 * it can be — by stubbing `matchMedia` so exactly one M3 query matches. That is the same
 * seam `WindowSizeClassService` reads in production, so the test drives the real
 * mechanism rather than a signal standing in for it.
 */
describe('Responsive shell (integration)', () => {
  let fixture: ComponentFixture<LayoutComponent>;
  let host: HTMLElement;

  /** Make `matchMedia` report a match for one M3 query and nothing else. */
  function stubSizeClass(size: WindowSizeClass): void {
    const target = WINDOW_SIZE_QUERIES[size];
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) =>
        ({
          matches: query === target,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    );
  }

  async function renderAt(size: WindowSizeClass): Promise<void> {
    stubSizeClass(size);

    await TestBed.configureTestingModule({
      imports: [LayoutComponent],
      providers: [
        provideRouter(routes),        ICON_PROVIDER,
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

    fixture = TestBed.createComponent(LayoutComponent);
    host = fixture.nativeElement;
    fixture.detectChanges();
    await fixture.whenStable();
  }

  afterEach(() => {
    vi.restoreAllMocks();
    TestBed.resetTestingModule();
  });

  it('when the window is compact, the navigation bar renders and the rail is a closed modal', async () => {
    await renderAt('compact');

    expect(host.querySelector('app-nav-bar')).toBeTruthy();

    const drawer = host.querySelector('mat-sidenav')!;
    expect(drawer.classList.contains('mat-drawer-over')).toBe(true);
    // A closed drawer is hidden from the accessibility tree by Material itself.
    expect(drawer.classList.contains('mat-drawer-opened')).toBe(false);
  });

  it('when the window is medium, the rail docks and the navigation bar is gone', async () => {
    await renderAt('medium');

    expect(host.querySelector('app-nav-bar')).toBeNull();

    const drawer = host.querySelector('mat-sidenav')!;
    expect(drawer.classList.contains('mat-drawer-side')).toBe(true);
    expect(host.querySelector('app-nav-rail')).toBeTruthy();
  });

  it('when the window is medium, the docked rail is collapsed and offers no expand toggle', async () => {
    await renderAt('medium');

    // M3 lists the expanded rail as modal-only at medium (layout/breakpoints.md:22),
    // so the toggle that would widen a docked one is not offered here.
    const drawer = host.querySelector('mat-sidenav')!;
    expect(drawer.classList.contains('rail-expanded')).toBe(false);
    expect(host.querySelector('app-nav-rail button[aria-label*="navigation"]')).toBeNull();
  });

  it('when the window is expanded, the docked rail offers the expand toggle', async () => {
    await renderAt('expanded');

    expect(host.querySelector('app-nav-rail button[aria-label="Expand navigation"]')).toBeTruthy();
  });

  it('when the window is extra-large, the rail starts expanded', async () => {
    await renderAt('extraLarge');

    const drawer = host.querySelector('mat-sidenav')!;
    expect(drawer.classList.contains('rail-expanded')).toBe(true);
    // The expanded rail lists its destinations in a mat-nav-list.
    expect(host.querySelector('app-nav-rail mat-nav-list')).toBeTruthy();
  });

  it('when the window is docked, the rail carries every destination', async () => {
    await renderAt('expanded');

    const railRoutes = Array.from(host.querySelectorAll('app-nav-rail a')).map((a) =>
      a.getAttribute('href'),
    );
    expect(railRoutes).toContain('/dashboard');
    expect(railRoutes).toContain('/asta');
    expect(railRoutes).toContain('/system');
  });

  it('when the shell renders, main is the skip-link target with a11y attributes', async () => {
    await renderAt('expanded');

    const main = host.querySelector('main');
    expect(main?.id).toBe('main-content');
    expect(main?.getAttribute('tabindex')).toBe('-1');
    expect(host.querySelector('a.skip-link[href="#main-content"]')).toBeTruthy();
  });

  it('when the shell renders, an aria-live announcer element exists', async () => {
    await renderAt('expanded');
    expect(host.querySelector('.sr-only[aria-live="polite"]')).toBeTruthy();
  });
});
