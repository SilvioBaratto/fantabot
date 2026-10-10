import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { DestroyRef, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../../environments/environment';
import { JobPanel, JobsService } from './jobs.service';

function panel(): JobPanel {
  return {
    running: signal(true),
    lines: signal<string[]>([]),
    status: signal('running'),
    ok: signal<boolean | null>(null),
    error: signal<string | null>(null),
  };
}

describe('JobsService.track', () => {
  let httpMock: HttpTestingController;
  let service: JobsService;
  const destroyRef = { onDestroy: () => () => undefined } as unknown as DestroyRef;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    httpMock = TestBed.inject(HttpTestingController);
    service = TestBed.inject(JobsService);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    httpMock.match(() => true);
  });

  it('ends the panel and calls onFinish when a poll fails', async () => {
    const p = panel();
    const onFinish = vi.fn();
    service.track({ jobId: 'J1', panel: p, destroyRef, onFinish });

    await vi.advanceTimersByTimeAsync(1600);
    httpMock
      .expectOne(`${environment.apiUrl}jobs/J1?since=0`)
      .flush('gone', { status: 404, statusText: 'Not Found' });
    await vi.advanceTimersByTimeAsync(0);

    expect(p.running()).toBe(false);
    expect(p.ok()).toBe(false);
    expect(p.status()).toBe('error');
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  it('lets a slow poll deliver instead of cancelling it on the next tick', async () => {
    const p = panel();
    service.track({ jobId: 'J2', panel: p, destroyRef });

    await vi.advanceTimersByTimeAsync(1600);
    const first = httpMock.expectOne(`${environment.apiUrl}jobs/J2?since=0`);
    await vi.advanceTimersByTimeAsync(1500);

    expect(first.cancelled).toBe(false);
    first.flush({ id: 'J2', status: 'running', lines: ['a'], ok: null, error: null });
    await vi.advanceTimersByTimeAsync(0);

    expect(p.lines()).toEqual(['a']);
  });
});
