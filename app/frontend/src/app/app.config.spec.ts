import { appConfig } from './app.config';
import { ICON_PROVIDER } from './icons';

describe('appConfig', () => {
  // Regression: icons.ts defined ICON_PROVIDER and icons.spec tested it in isolation,
  // but app.config never added it to providers — so at runtime every lucide icon threw
  // "The '<name>' icon has not been provided by any available icon providers" and the nav
  // rendered blank. This asserts the provider is actually wired into the app config.
  it('registers the lucide icon provider so icons render at runtime', () => {
    expect(appConfig.providers).toContain(ICON_PROVIDER);
  });
});
