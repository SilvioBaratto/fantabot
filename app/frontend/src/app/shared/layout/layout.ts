import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { Title } from '@angular/platform-browser';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconButton } from '@angular/material/button';
import { LucideAngularModule } from 'lucide-angular';
import { filter, map } from 'rxjs/operators';

import { NAV_ITEMS } from '../nav-item';
import { WindowSizeClassService } from '../../core/window-size-class';
import { NavBarComponent } from '../nav-bar/nav-bar';
import { NavRailComponent } from '../nav-rail/nav-rail';

/**
 * The application shell: one navigation system per width, and one flexible pane.
 *
 * | Window size | Navigation |
 * |---|---|
 * | compact (< 600) | navigation bar, plus a **modal** rail holding only what the bar lacks |
 * | medium (600–839) | collapsed rail, docked |
 * | expanded (840+) | collapsed rail, docked, with an expand toggle |
 * | extra-large (1600+) | expanded rail by default; the toggle can still collapse it |
 *
 * Two things here are decisions rather than plumbing.
 *
 * **The breakpoint is 600, not 768.** The shell this replaced switched at Tailwind's `md`
 * and showed a bottom tab bar *and* a hamburger drawer below it — two primary navigation
 * systems at one width, listing the same ten destinations. M3 has no such pattern: a bar
 * and a modal rail may coexist only when the rail holds destinations the bar does not
 * (`layout/breakpoints.md:22`). `overflowItems` is the exact complement of `barItems`, so
 * the overlap cannot come back.
 *
 * **Focus moves in code, not through the fragment.** `index.html` sets `<base href="/">`,
 * which turns a bare `href="#main-content"` into a route change that reloads the app. The
 * anchor keeps its href so the affordance is real and discoverable, and the click handler
 * takes it from there.
 */
@Component({
  selector: 'app-layout',
  imports: [
    RouterOutlet,
    MatSidenavModule,
    MatToolbarModule,
    MatIconButton,
    LucideAngularModule,
    NavBarComponent,
    NavRailComponent,
  ],
  templateUrl: './layout.html',
  styleUrl: './layout.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LayoutComponent {
  private readonly router = inject(Router);
  private readonly title = inject(Title);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly size = inject(WindowSizeClassService).current;

  protected readonly navItems = NAV_ITEMS;
  protected readonly barItems = NAV_ITEMS.filter((i) => i.inBar);
  protected readonly overflowItems = NAV_ITEMS.filter((i) => !i.inBar);

  /** Compact only: the modal rail behind the menu button. */
  protected readonly modalOpen = signal(false);

  /** null = follow the size class; a boolean = the operator's own choice, which holds. */
  private readonly expandedChoice = signal<boolean | null>(null);

  protected readonly usesBar = computed(() => this.size() === 'compact');
  protected readonly railMode = computed(() => (this.usesBar() ? 'over' : 'side'));
  protected readonly railOpened = computed(() => !this.usesBar() || this.modalOpen());

  /** A docked *expanded* rail is listed from `expanded` up; medium docks only a collapsed one. */
  protected readonly canDockExpanded = computed(() =>
    ['expanded', 'large', 'extraLarge'].includes(this.size()),
  );
  protected readonly railExpanded = computed(
    () => this.expandedChoice() ?? this.size() === 'extraLarge',
  );
  /** The modal rail is always the expanded form; a docked one widens only where M3 allows it. */
  protected readonly railWide = computed(
    () => this.usesBar() || (this.canDockExpanded() && this.railExpanded()),
  );

  /** Announced to screen readers after each route change. */
  protected readonly announced = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map(() => this.title.getTitle()),
    ),
    { initialValue: '' },
  );

  constructor() {
    this.router.events
      .pipe(
        filter((e): e is NavigationEnd => e instanceof NavigationEnd),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.modalOpen.set(false);
        this.focusMain();
      });
  }

  protected toggleModalRail(): void {
    this.modalOpen.update((v) => !v);
  }

  protected closeModalRail(): void {
    this.modalOpen.set(false);
  }

  protected toggleRailExpanded(): void {
    this.expandedChoice.set(!this.railExpanded());
  }

  /** The skip link's real behaviour; see the class docstring for why it is not the href. */
  protected skipToMain(event: Event): void {
    event.preventDefault();
    this.focusMain();
  }

  private focusMain(): void {
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }
}
