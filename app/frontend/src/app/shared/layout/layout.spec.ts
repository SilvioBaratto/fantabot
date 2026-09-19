import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { LucideIconConfig } from 'lucide-angular';

import { ICON_PROVIDER } from '../../icons';
import { NAV_ITEMS } from '../nav-item';
import { LayoutComponent } from './layout';

/**
 * The shell's structure and a11y plumbing.
 *
 * `matchMedia` is stubbed to match nothing, which `WindowSizeClassService` resolves to
 * `compact` — so these render the compact arrangement: a navigation bar, and a modal rail
 * that is closed. The docked-rail arrangement is covered in the integration spec.
 */
/**
 * Match nothing, which `WindowSizeClassService` resolves to `compact`.
 * `addListener` is the deprecated API the CDK's `BreakpointObserver` still calls, so a
 * stub without it throws before any assertion runs.
 */
function stubCompact(): void {
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: false,
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

describe('LayoutComponent', () => {
  beforeEach(async () => {
    stubCompact();

    await TestBed.configureTestingModule({
      imports: [LayoutComponent],
      providers: [
        provideRouter([]),        ICON_PROVIDER,
        {
          provide: LucideIconConfig,
          useFactory: () => {
            const cfg = new LucideIconConfig();
            cfg.size = 16;
            cfg.strokeWidth = 1.5;
            return cfg;
          },
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(LayoutComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture.nativeElement;
  }

  it('when the shell renders, the navigation rail is hosted in a sidenav', async () => {
    const el = await render();
    expect(el.querySelector('mat-sidenav app-nav-rail')).toBeTruthy();
  });

  it('when the window is compact, the navigation bar renders', async () => {
    const el = await render();
    expect(el.querySelector('app-nav-bar')).toBeTruthy();
  });

  it('when the shell renders, main is the skip-link target with a11y attributes', async () => {
    const el = await render();
    const main = el.querySelector('main');
    expect(main?.id).toBe('main-content');
    expect(main?.getAttribute('tabindex')).toBe('-1');
    expect(main?.getAttribute('aria-label')).toBeTruthy();
  });

  it('when the shell renders, an aria-live route announcer is present', async () => {
    const el = await render();
    expect(el.querySelector('.sr-only[aria-live="polite"]')).toBeTruthy();
  });

  it('when the shell renders, an inert toast outlet is present', async () => {
    const el = await render();
    expect(el.querySelector('#toast-outlet')).toBeTruthy();
  });

  it('when the shell renders, a skip link pointing to main-content is present', async () => {
    const el = await render();
    expect(el.querySelector('a.skip-link[href="#main-content"]')).toBeTruthy();
  });

  it('when the skip link is clicked, focus moves to main without navigating', async () => {
    const el = await render();
    const link = el.querySelector('a.skip-link') as HTMLAnchorElement;
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });

    link.dispatchEvent(event);

    // `<base href="/">` makes a bare fragment href a route change, so the handler
    // must cancel it and move focus itself.
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(el.querySelector('#main-content'));
  });

  /**
   * The bar and the modal rail may coexist at compact only while they name different
   * destinations (`layout/breakpoints.md:22`). The previous shell failed this: a bottom
   * tab bar and a hamburger drawer, both listing all ten.
   */
  it('when the window is compact, the bar and the modal rail share no destination', async () => {
    const el = await render();
    const barRoutes = Array.from(el.querySelectorAll('app-nav-bar a')).map((a) =>
      a.getAttribute('href'),
    );
    const railRoutes = Array.from(el.querySelectorAll('app-nav-rail a')).map((a) =>
      a.getAttribute('href'),
    );

    expect(barRoutes.length).toBeGreaterThan(0);
    expect(railRoutes.length).toBeGreaterThan(0);
    expect(barRoutes.filter((r) => railRoutes.includes(r))).toEqual([]);
    expect(barRoutes.length + railRoutes.length).toBe(NAV_ITEMS.length);
  });

  it('when the window is compact, the bar holds at most five destinations', async () => {
    const el = await render();
    const tabs = el.querySelectorAll('app-nav-bar a');
    // M3 navigation bar: 3–5 destinations (layout/scaffold.md:67).
    expect(tabs.length).toBeGreaterThanOrEqual(3);
    expect(tabs.length).toBeLessThanOrEqual(5);
  });

  it('when navigation ends, focus moves to main-content', async () => {
    const routes = [{ path: 'dashboard', component: LayoutComponent }];
    stubCompact();
    await TestBed.configureTestingModule({
      imports: [LayoutComponent],
      providers: [
        provideRouter(routes),        ICON_PROVIDER,
        {
          provide: LucideIconConfig,
          useFactory: () => {
            const cfg = new LucideIconConfig();
            cfg.size = 16;
            cfg.strokeWidth = 1.5;
            return cfg;
          },
        },
      ],
    }).compileComponents();

    const router = TestBed.inject(Router);
    const fixture = TestBed.createComponent(LayoutComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    const main = fixture.nativeElement.querySelector('#main-content') as HTMLElement;
    expect(main).toBeTruthy();

    await router.navigate(['/dashboard']);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(document.activeElement).toBe(main);
  });
});
