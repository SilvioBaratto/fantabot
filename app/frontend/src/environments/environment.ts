import type { AppEnvironment } from './environment.model';

/**
 * The one environment file, dev and production alike: a relative path.
 *
 * There used to be an `environment.prod.ts` swapped in by an `angular.json`
 * `fileReplacements` entry, and it set the same `apiUrl` this one does — a replacement
 * that replaced nothing. Both are gone; the production build reads this file.
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
