import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

/** What one confirmation is about. `id` is what the page's `data-confirm-*` hook carries. */
export interface DisconnectRequest {
  readonly kind: 'league' | 'fantalab';
  readonly id: string;
  /** The credential as the operator knows it — a lega name, or a FantaLab user id. */
  readonly name: string;
}

/**
 * The confirmation for the most destructive action in the app.
 *
 * `DELETE /auth/league/{id}` purges a lega across six tables, so the body says what is
 * lost rather than asking a bare "are you sure?". Three things here are decisions:
 *
 * **It is an `alertdialog`, not a dialog.** The page opens it with `role: 'alertdialog'`
 * so the whole body is announced on open, not just the title.
 *
 * **Keep comes first in the DOM.** `autoFocus: 'first-tabbable'` is Material's default,
 * so DOM order is what decides where a blind Enter lands — and it must not land on the
 * destructive button.
 *
 * **Only the confirm is in `.danger-zone`.** The helper remaps `primary` onto the error
 * roles for its subtree, so a filled button inside it is error-coloured without a hex
 * anywhere. Wrapping the whole panel would recolour Keep too, which is the escape route.
 */
@Component({
  selector: 'app-disconnect-dialog',
  imports: [MatDialogModule, MatButtonModule],
  templateUrl: './disconnect-dialog.html',
  styleUrl: './disconnect-dialog.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DisconnectDialogComponent {
  readonly data = inject<DisconnectRequest>(MAT_DIALOG_DATA);
}
