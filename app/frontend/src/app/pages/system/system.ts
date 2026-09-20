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

import { DbDumpService } from '../../core/api/db-dump.service';
import { DbHealthService } from '../../core/api/db-health.service';
import { JobsService } from '../../core/api/jobs.service';
import { SystemConfigService } from '../../core/api/system-config.service';
import { DbHealth } from '../../core/models/db-health';
import { DumpTarget } from '../../core/models/db-dump';
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
  private readonly dumpService = inject(DbDumpService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly health = signal<DbHealth | null>(null);
  readonly config = signal<SystemConfig | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  /** Where today's dump would land, or why nowhere would do. Read before it is offered. */
  readonly dumpTarget = signal<DumpTarget | null>(null);
  readonly dumping = signal(false);
  /** The last failed dump's own words, from the job log. Cleared when a new one starts. */
  readonly dumpError = signal<string | null>(null);

  /**
   * Whether there is anything to offer.
   *
   * A refused target has **no path**, so there is no disabled button beside one: a
   * greyed-out control next to a filename is a screen claiming a file it will never
   * write. The template drops the control instead and renders the reason.
   */
  readonly canDump = computed(() => !!this.dumpTarget()?.path);

  /** `412000000` -> `393 MB`. Binary, because that is what the command prints. */
  readonly dumpSize = computed(() => {
    const bytes = this.dumpTarget()?.size_bytes;
    if (bytes === null || bytes === undefined) return '';
    return `${Math.round(bytes / 1_048_576)} MB`;
  });

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

  /**
   * Start the dump, then follow the job until it stops.
   *
   * Polled by hand rather than on an `interval` like `synchronize.ts`: there is no log to
   * stream here, only a status, and the thing worth showing at the end is the file — so
   * the last act is to re-read the target rather than to render the last line.
   */
  startDump(): void {
    if (!this.canDump() || this.dumping()) return;
    this.dumping.set(true);
    this.dumpError.set(null);
    this.dumpService
      .run()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (started) => {
          if (started.outcome !== 'started') {
            // The target moved under us between the read and the click — a `$HOME` that
            // changed, or an app restarted elsewhere. The refusal is the answer.
            this.dumping.set(false);
            this.dumpTarget.set({
              path: '',
              refused: started.detail,
              exists: false,
              size_bytes: null,
            });
            return;
          }
          this.followDump(started.job_id);
        },
        error: () => {
          this.dumping.set(false);
          this.dumpError.set("Couldn't reach the API.");
        },
      });
  }

  private followDump(jobId: string): void {
    this.jobs
      .get(jobId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (job) => {
          if (job.status === 'running') {
            setTimeout(() => this.followDump(jobId), 1500);
            return;
          }
          this.dumping.set(false);
          // `ok === false` is `pg_dump` exiting non-zero, and the child has already
          // printed the sentence that says which of the two failures it was. Nothing is
          // left at the path — `run_dump` removes a dump that did not finish — so the
          // re-read below is also what proves the screen is not naming a phantom.
          if (job.ok === false) {
            this.dumpError.set(job.lines[job.lines.length - 1] ?? job.error ?? 'The dump failed.');
          }
          this.loadDumpTarget();
        },
        error: () => {
          this.dumping.set(false);
          this.dumpError.set("Couldn't reach the API.");
        },
      });
  }

  private loadDumpTarget(): void {
    this.dumpService
      .target()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (target) => this.dumpTarget.set(target),
        error: () => this.dumpTarget.set(null),
      });
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
    // Third request, same rule: its own failure. The dump card is the one thing on this
    // page that is useful *because* the database is not answering.
    this.loadDumpTarget();
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
