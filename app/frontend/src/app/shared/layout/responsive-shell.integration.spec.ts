import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { LucideIconConfig } from 'lucide-angular';

import { ICON_PROVIDER } from '../../icons';
import { routes } from '../../app.routes';
import { LayoutComponent } from './layout';

/**
 * Cross-component integration: the responsive shell's breakpoint visibility and
 * a11y plumbing. jsdom does not evaluate CSS `md:` media queries, so visibility
 * is asserted structurally — the `isMobile` signal drives the sidebar's drawer
 * state, and the bottom-tab-bar carries the `md:hidden` class that hides it on
 * desktop. The real width sources (`ResizeObserver`, `innerWidth`) are stubbed
 * so `isMobile` is driven only by the signal — deterministic, per issue notes.
 */
describe('Responsive shell (integration)', () => {
  let fixture: ComponentFixture<LayoutComponent>;
  let component: LayoutComponent;
  let host: HTMLElement;
  let originalResizeObserver: typeof ResizeObserver;

  beforeEach(async () => {
    originalResizeObserver = window.ResizeObserver;
    window.ResizeObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    } as unknown as typeof ResizeObserver;
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1024);
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    } as unknown as MediaQueryList);

    await TestBed.configureTestingModule({
      imports: [LayoutComponent],
      providers: [
        provideRouter(routes),
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

    fixture = TestBed.createComponent(LayoutComponent);
    component = fixture.componentInstance;
    host = fixture.nativeElement;
  });

  afterEach(() => {
    window.ResizeObserver = originalResizeObserver;
    document.documentElement.classList.remove('dark', 'theme-transitioning');
  });

  /** Run ngOnInit once, then drive the signals and re-render deterministically. */
  async function renderWith(mobile: boolean, open = false): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    component.isMobile.set(mobile);
    component.isSidebarOpen.set(open);
    fixture.detectChanges();
    await fixture.whenStable();
  }

  it('when isMobile is true, the bottom-tab-bar renders and the fixed sidebar is collapsed', async () => {
    await renderWith(true, false);

    expect(host.querySelector('app-bottom-tab-bar')).toBeTruthy();
    const aside = host.querySelector('app-sidebar aside')!;
    expect(aside.classList.contains('-translate-x-full')).toBe(true);
    expect(aside.classList.contains('translate-x-0')).toBe(false);
  });

  it('when isMobile is false, the fixed sidebar is visible and the bottom-tab-bar is hidden on desktop', async () => {
    await renderWith(false);

    const aside = host.querySelector('app-sidebar aside')!;
    expect(aside.classList.contains('translate-x-0')).toBe(true);
    const tabBar = host.querySelector('app-bottom-tab-bar')!;
    expect(tabBar.classList.contains('md:hidden')).toBe(true);
  });

  it('when the shell renders, main is the skip-link target with a11y attributes', async () => {
    await renderWith(false);

    const main = host.querySelector('main');
    expect(main?.id).toBe('main-content');
    expect(main?.getAttribute('tabindex')).toBe('-1');
    expect(host.querySelector('a.skip-link[href="#main-content"]')).toBeTruthy();
  });

  it('when the shell renders, an aria-live announcer element exists', async () => {
    await renderWith(false);
    expect(host.querySelector('.sr-only[aria-live="polite"]')).toBeTruthy();
  });
});
