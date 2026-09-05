import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { Toast, ToastService, ToastVariant } from './toast.service';

const VARIANT_CLASSES: Record<ToastVariant, string> = {
  info: 'bg-info/10 border-info/30 text-info',
  success: 'bg-success/10 border-success/30 text-success',
  warning: 'bg-warning/10 border-warning/30 text-warning',
  error: 'bg-danger/10 border-danger/30 text-danger',
};

const ITEM_BASE =
  'flex items-center w-80 rounded-lg border px-4 py-3 shadow-md animate-fade-in-up';

@Component({
  selector: 'app-toast',
  templateUrl: './toast.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ToastComponent {
  private readonly toastService = inject(ToastService);

  readonly politeToasts = computed<Toast[]>(() =>
    this.toastService.toasts().filter((t) => t.variant !== 'error'),
  );

  readonly errorToasts = computed<Toast[]>(() =>
    this.toastService.toasts().filter((t) => t.variant === 'error'),
  );

  itemClasses(variant: ToastVariant): string {
    return `${ITEM_BASE} ${VARIANT_CLASSES[variant]}`;
  }

  dismiss(id: number): void {
    this.toastService.dismiss(id);
  }
}
