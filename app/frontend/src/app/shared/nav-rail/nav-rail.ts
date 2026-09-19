import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { MatListModule } from '@angular/material/list';
import { MatIconButton } from '@angular/material/button';
import { LucideAngularModule } from 'lucide-angular';

import { NavItem } from '../nav-item';

/**
 * The M3 navigation rail, collapsed or expanded.
 *
 * Angular Material has no rail. Collapsed items are the navigation bar's items stacked
 * (96px, icon over label); expanded items are a `mat-nav-list`, whose active indicator is
 * already `secondary-container` and fully rounded. The shell hosts this in a
 * `mat-sidenav`, which brings the scrim, focus trap, Esc and focus restore for the modal
 * case — so this component owns no drawer mechanics of its own.
 *
 * **Not a navigation drawer.** M3's breakpoint guidance uses a bar or a rail at every
 * size and never a permanent drawer (`layout/breakpoints.md:22`), which is what this
 * replaces.
 */
@Component({
  selector: 'app-nav-rail',
  imports: [RouterLink, RouterLinkActive, MatListModule, MatIconButton, LucideAngularModule],
  templateUrl: './nav-rail.html',
  styleUrl: './nav-rail.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NavRailComponent {
  readonly items = input.required<NavItem[]>();
  readonly expanded = input(false);

  /** The expand/collapse toggle only docks a wider rail from `expanded` up, so the shell
   * hides it at compact and medium (`layout/breakpoints.md:22`). */
  readonly showToggle = input(false);

  /** Emitted when a destination is chosen, so a modal rail can close itself. */
  readonly navigated = output<void>();
  readonly toggleExpanded = output<void>();
}
