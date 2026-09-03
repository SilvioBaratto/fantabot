import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-navbar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="flex items-center justify-between h-14 px-4
                   border-b border-border bg-surface-raised shrink-0
                   pt-safe">
      <div class="flex items-center gap-2 min-w-0">
        <ng-content select="[navbarBrand]" />
      </div>
      <div class="flex items-center gap-1">
        <ng-content select="[navbarActions]" />
      </div>
    </header>
  `,
})
export class NavbarComponent {}
