import { TestBed } from '@angular/core/testing';
import {
  HttpClient,
  HttpErrorResponse,
  provideHttpClient,
  withFetch,
  withInterceptors,
} from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';

import { errorInterceptor } from './error.interceptor';

describe('errorInterceptor', () => {
  let http: HttpClient;
  let controller: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withFetch(), withInterceptors([errorInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    controller = TestBed.inject(HttpTestingController);
  });

  afterEach(() => controller.verify());

  it('when a request fails, the error is rethrown to the caller', async () => {
    let caughtError: HttpErrorResponse | undefined;

    const request$ = firstValueFrom(
      http.get('/api/test').pipe(
        // swallow for the assertion — we only care that the error propagates
      ),
    ).catch((err: HttpErrorResponse) => {
      caughtError = err;
    });

    controller.expectOne('/api/test').flush('Server error', {
      status: 500,
      statusText: 'Internal Server Error',
    });

    await request$;

    expect(caughtError).toBeInstanceOf(HttpErrorResponse);
    expect(caughtError!.status).toBe(500);
  });

  it('when a request fails, the interceptor does not swallow the error', async () => {
    let resolved = false;
    let rejected = false;

    firstValueFrom(http.get('/api/test')).then(
      () => { resolved = true; },
      () => { rejected = true; },
    );

    controller.expectOne('/api/test').flush('Not Found', {
      status: 404,
      statusText: 'Not Found',
    });

    await TestBed.inject(Promise as never, { optional: true });
    // allow microtask queue to drain
    await new Promise<void>((r) => setTimeout(r, 0));

    expect(resolved).toBe(false);
    expect(rejected).toBe(true);
  });
});
