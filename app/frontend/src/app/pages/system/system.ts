import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { LucideAngularModule } from 'lucide-angular';

import { DbHealthService } from '../../core/api/db-health.service';
import { SystemConfigService } from '../../core/api/system-config.service';
import { DbHealth } from '../../core/models/db-health';
import { SystemConfig } from '../../core/models/system-config';

/** One resolved setting, ready to render. */
export interface SettingRow {
  key: string;
  value: string;
}

/** One secret, reported as present or absent — never as a value. */
export interface SecretRow {
  key: string;
  isSet: boolean;
}

@Component({
  selector: 'app-system',
  imports: [LucideAngularModule, DecimalPipe, MatButtonModule, MatCardModule, MatProgressBarModule],
  templateUrl: './system.html',
  styleUrl: './system.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SystemComponent implements OnInit {
  private readonly service = inject(DbHealthService);
  private readonly configService = inject(SystemConfigService);
  private readonly destroyRef = inject(DestroyRef);

  readonly health = signal<DbHealth | null>(null);
  readonly config = signal<SystemConfig | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  /**
   * The settings, sorted and stringified.
   *
   * Sorted because `model_dump` returns declaration order, which is the order the Python
   * file happens to be written in — an operator scanning for one key should not have to
   * know that. Stringified here rather than in the template so `false` and `0` render as
   * themselves: an empty cell where a setting reads `false` is the same screen as a
   * setting that is missing, and this page exists to tell those apart.
   */
  readonly settingRows = computed<SettingRow[]>(() => {
    const settings = this.config()?.settings ?? {};
    return Object.keys(settings)
      .sort()
      .map((key) => ({ key, value: this.render(settings[key]) }));
  });

  readonly secretRows = computed<SecretRow[]>(() => {
    const secrets = this.config()?.secrets_set ?? {};
    return Object.keys(secrets)
      .sort()
      .map((key) => ({ key, isSet: secrets[key] }));
  });

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getHealth()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (health) => {
          this.health.set(health);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set("Couldn't reach the API.");
          this.loading.set(false);
        },
      });
    // Its own request and its own failure. The health probe is the one that reports a
    // database that will not open, so a config read that fails must not take it down with
    // it — and the config is the screen an operator opens *because* the database is not
    // answering, which is exactly when a shared failure would hide it.
    this.configService
      .getConfig()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (config) => this.config.set(config),
        error: () => this.config.set(null),
      });
  }

  /** `unknown` -> something readable, without turning a real value into an empty cell. */
  private render(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (value === '') return '(empty)';
    if (Array.isArray(value)) return value.length ? value.join(', ') : '(empty)';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }
}
