import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

@Component({
  selector: 'app-hamburger',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LucideAngularModule],
  template: `
    <button
      type="button"
      class="inline-flex items-center justify-center min-h-11 min-w-11
             rounded-md p-1.5 text-text-secondary
             hover:text-text hover:bg-surface-inset
             transition-colors focus-visible:outline-none
             focus-visible:ring-2 focus-visible:ring-primary"
      [attr.aria-expanded]="open()"
      [attr.aria-controls]="controls()"
      [attr.aria-label]="label()"
      (click)="toggle.emit()"
    >
      <lucide-icon [name]="open() ? 'X' : 'Menu'" class="w-5 h-5" aria-hidden="true" />
    </button>
  `,
})
export class HamburgerComponent {
  readonly open     = input(false);
  readonly controls = input('');

  readonly toggle = output<void>();

  readonly label = computed(() => this.open() ? 'Close menu' : 'Open menu');
}
