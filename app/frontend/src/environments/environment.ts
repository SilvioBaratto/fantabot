import type { AppEnvironment } from './environment.model';

/**
 * Dev talks to the API the same way production does: a relative path.
 *
 * It used to be the absolute `http://127.0.0.1:8000/api/v1/`, which only ever worked in a
 * browser running on the same machine as the API. Opened from a phone, a tablet or another
 * box on the tailnet, the page loaded and then every request went to *that* device's own
 * port 8000, where nothing is listening — a working shell with no data in it.
 *
 * `proxy.conf.json` now forwards `/api` from the dev server to `127.0.0.1:8000`, so the
 * browser only ever talks to the origin it was served from. That also keeps the API bound
 * to loopback and takes CORS out of the picture entirely, since it is now same-origin.
 */
export const environment: AppEnvironment = {
  apiUrl: '/api/v1/',
};
