import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';

import { NavItem } from '../nav-item';

/**
 * The M3 navigation bar — compact only (< 600px).
 *
 * Angular Material ships no navigation bar, so it is built from `--mat-sys-*` tokens:
 * an `secondary-container` active indicator, Material's own state-layer opacities, and a
 * focus ring drawn outside the pill. The dimensions come from the M3 navigation bar
 * component spec, not from the foundation pages, which give none.
 *
 * It renders whatever it is handed. The shell decides that the list is the `inBar`
 * subset, so this component cannot be the place the bar and the rail drift apart.
 */
@Component({
  selector: 'app-nav-bar',
  imports: [RouterLink, RouterLinkActive, LucideAngularModule],
  templateUrl: './nav-bar.html',
  styleUrl: './nav-bar.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NavBarComponent {
  readonly items = input.required<NavItem[]>();
}
