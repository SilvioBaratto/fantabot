import { Injectable, computed, inject } from '@angular/core';
import { BreakpointObserver } from '@angular/cdk/layout';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs/operators';

/**
 * M3 window size classes (`layout/breakpoints.md:9`). On the web 1dp = 1 CSS px.
 *
 * **Not the CDK presets.** `Breakpoints.XSmall/Small/Medium/Large/XLarge` are 600/960/
 * 1280/1920 — M2-era numbers — and `Handset` folds in orientation. Using them is how a
 * layout ends up switching at 960 while its CSS switches at 840.
 *
 * The literals are repeated in CSS because custom properties cannot appear inside a media
 * query. Keep the two in step: 600 / 840 / 1200 / 1600.
 */
export const WINDOW_SIZE_QUERIES = {
  compact: '(max-width: 599.98px)',
  medium: '(min-width: 600px) and (max-width: 839.98px)',
  expanded: '(min-width: 840px) and (max-width: 1199.98px)',
  large: '(min-width: 1200px) and (max-width: 1599.98px)',
  extraLarge: '(min-width: 1600px)',
} as const;

export type WindowSizeClass = keyof typeof WINDOW_SIZE_QUERIES;

/**
 * Prefer plain CSS media queries for anything that only changes styles — no flash on
 * first paint, nothing to hydrate. Inject this only where the *component tree* differs:
 * a navigation bar instead of a rail, a bottom sheet instead of a menu.
 */
@Injectable({ providedIn: 'root' })
export class WindowSizeClassService {
  private readonly observer = inject(BreakpointObserver);

  readonly current = toSignal(
    this.observer.observe(Object.values(WINDOW_SIZE_QUERIES)).pipe(map(() => this.match())),
    { initialValue: this.match() },
  );

  /** One flexible pane below 840px; two are recommended from there up. */
  readonly twoPane = computed(() => this.current() !== 'compact' && this.current() !== 'medium');

  private match(): WindowSizeClass {
    const classes = Object.keys(WINDOW_SIZE_QUERIES) as WindowSizeClass[];
    // A test environment's matchMedia stub matches nothing; compact is the safe floor.
    return classes.find((c) => this.observer.isMatched(WINDOW_SIZE_QUERIES[c])) ?? 'compact';
  }
}
