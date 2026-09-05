import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';

import { HarvestService } from '../../core/api/harvest.service';
import { Corpus } from '../../core/models/corpus';

/**
 * What the harvest actually put in the database, per format.
 *
 * Built before anything with a lifecycle, and `todo/TODO.md` §2 says why: it is the
 * instrument every later increment is graded on, and without it "collection worked" is
 * unfalsifiable. §1.1 is the case in point — the Classic corpus read as 2.1 million
 * events and zero sales for over a week, and no screen could tell "nothing was
 * collected" from "everything was collected and nothing joined".
 */
@Component({
  selector: 'app-harvest',
  imports: [LucideAngularModule, DecimalPipe],
  templateUrl: './harvest.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class HarvestComponent implements OnInit {
  private readonly service = inject(HarvestService);
  private readonly destroyRef = inject(DestroyRef);

  readonly corpus = signal<Corpus | null>(null);
  readonly loading = signal(true);
  /** The API could not be reached at all — distinct from a corpus that read `ok: false`. */
  readonly errorMsg = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getCorpus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (corpus) => {
          this.corpus.set(corpus);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }
}
