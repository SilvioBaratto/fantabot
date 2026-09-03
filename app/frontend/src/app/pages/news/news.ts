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
import { forkJoin } from 'rxjs';

import { NewsService } from '../../core/api/news.service';
import { DriftRow, NewsRow } from '../../core/models/news';

@Component({
  selector: 'app-news',
  imports: [LucideAngularModule, DecimalPipe],
  templateUrl: './news.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class NewsComponent implements OnInit {
  private readonly service = inject(NewsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly feed = signal<NewsRow[]>([]);
  readonly drifted = signal<DriftRow[]>([]);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);
  readonly loaded = signal(false);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    forkJoin({ feed: this.service.getFeed(50), drifted: this.service.getDrifted() })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ feed, drifted }) => {
          this.feed.set(feed);
          this.drifted.set(drifted);
          this.loaded.set(true);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }

  sentimentTone(value: number): 'up' | 'down' | 'flat' {
    if (value > 0.05) return 'up';
    if (value < -0.05) return 'down';
    return 'flat';
  }
}
