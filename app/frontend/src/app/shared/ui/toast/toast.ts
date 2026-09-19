import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  untracked,
} from '@angular/core';
import { MatIconButton } from '@angular/material/button';
import {
  MAT_SNACK_BAR_DATA,
  MatSnackBar,
  MatSnackBarActions,
  MatSnackBarLabel,
  MatSnackBarRef,
} from '@angular/material/snack-bar';
import { LucideAngularModule } from 'lucide-angular';

import { Toast, ToastService, ToastVariant } from './toast.service';

/* A status glyph for the three variants that carry one. `info` is the plain snackbar:
 * M3's snackbar has no leading icon, and adding one to every toast would make the
 * neutral case shout. Shape, not colour, is what tells the variants apart here — the
 * container stays inverse-surface for all four, so an "error" that read only as red
 * would not read at all. Names are the kebab-case form `LucideIconProvider` resolves
 * against `icons.ts`; all three are already registered there. */
const VARIANT_ICON: Record<ToastVariant, string | null> = {
  info: null,
  success: 'check-circle',
  warning: 'triangle-alert',
  error: 'circle-x',
};

/* Only an error interrupts what a screen reader is already saying. Leave
 * `announcementMessage` unset alongside this: setting it makes the container fall back
 * to a polite live region and hand the announcement to `LiveAnnouncer` instead. */
const VARIANT_POLITENESS: Record<ToastVariant, 'polite' | 'assertive'> = {
  info: 'polite',
  success: 'polite',
  warning: 'polite',
  error: 'assertive',
};

/**
 * What is drawn inside the snackbar: the message, its variant glyph, and one close
 * button. Rendered into the CDK overlay, so it is the only place around the panel whose
 * styles can be component-scoped.
 */
@Component({
  selector: 'app-toast-snack-bar',
  imports: [MatSnackBarLabel, MatSnackBarActions, MatIconButton, LucideAngularModule],
  templateUrl: './toast.html',
  styles: `
    :host {
      display: flex;
      flex: 1 1 auto;
      align-items: center;
      min-inline-size: 0;
    }

    .toast-label {
      display: flex;
      flex: 1 1 auto;
      gap: 12px;
      align-items: center;
      min-inline-size: 0;
      /* A long message scrolls rather than pushing the snackbar off-screen; no fixed
         height, so the text still grows to 200%. */
      max-block-size: 50vh;
      overflow: auto;
    }

    .toast-icon {
      display: flex;
      flex-shrink: 0;
      color: var(--mat-sys-inverse-on-surface);
    }

    .toast-message {
      min-inline-size: 0;
    }

    .toast-actions {
      align-items: center;
    }

    /* An icon button is not the snackbar's text action, so Material's
       inverse-primary rule does not reach it and it would keep the ordinary
       on-surface-variant icon colour — dark on the inverted container. Re-point the
       component's own tokens instead of overriding its rules. */
    .toast-close {
      flex-shrink: 0;
      --mat-icon-button-icon-color: var(--mat-sys-inverse-on-surface);
      --mat-icon-button-state-layer-color: var(--mat-sys-inverse-on-surface);
      --mat-icon-button-ripple-color: color-mix(
        in srgb,
        var(--mat-sys-inverse-on-surface) calc(var(--mat-sys-pressed-state-layer-opacity) * 100%),
        transparent
      );
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ToastSnackBarComponent {
  private readonly toastService = inject(ToastService);

  readonly toast = inject<Toast>(MAT_SNACK_BAR_DATA);
  readonly icon = VARIANT_ICON[this.toast.variant];

  /** Dismiss through the service, never through the ref: the list is the single owner
   *  of a toast's life, and `ToastComponent` closes the panel when the entry goes. */
  dismiss(): void {
    this.toastService.dismiss(this.toast.id);
  }
}

/**
 * The toast outlet. Draws nothing itself — it presents `ToastService`'s list on
 * Material's snackbar, which lives in the CDK overlay.
 *
 * `MatSnackBar` shows one snackbar at a time by design, so the list is a queue: the
 * oldest entry is on screen and the rest wait their turn. The clock stays in the
 * service (`duration: 0` here), which keeps one dismissal path — the timer, the close
 * button and any caller holding an id all go through `dismiss(id)`, and the panel
 * follows the list rather than racing it.
 */
@Component({
  selector: 'app-toast',
  template: '',
  styles: ':host { display: contents; }',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ToastComponent {
  private readonly toastService = inject(ToastService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly destroyRef = inject(DestroyRef);

  private ref: MatSnackBarRef<ToastSnackBarComponent> | null = null;
  private shownId: number | null = null;

  constructor() {
    effect(() => {
      const head = this.toastService.toasts()[0] ?? null;
      untracked(() => this.present(head));
    });

    this.destroyRef.onDestroy(() => this.close());
  }

  private present(next: Toast | null): void {
    if (next === null) {
      this.close();
      return;
    }
    if (this.shownId === next.id) return;

    this.shownId = next.id;
    const ref = this.snackBar.openFromComponent(ToastSnackBarComponent, {
      data: next,
      // The service owns the clock. A snackbar duration would be a second timer with
      // its own idea of when the toast ended.
      duration: 0,
      politeness: VARIANT_POLITENESS[next.variant],
      panelClass: ['app-toast', `app-toast-${next.variant}`],
    });
    this.ref = ref;

    ref.afterDismissed().subscribe(() => {
      // A later toast replaced this panel: the entry it belonged to is already gone.
      if (this.ref !== ref) return;
      this.ref = null;
      this.shownId = null;
      this.toastService.dismiss(next.id);
    });
  }

  private close(): void {
    const ref = this.ref;
    if (ref === null) return;
    // Cleared first, so the `afterDismissed` handler above knows this close came from
    // the list and does not dismiss the entry a second time.
    this.ref = null;
    this.shownId = null;
    ref.dismiss();
  }
}
