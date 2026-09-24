import { TestBed } from '@angular/core/testing';
import { App } from './app';

/**
 * **Kept, and measured before it was.**
 *
 * `expect(app).toBeTruthy()` on a component whose body is `export class App {}` reads as a
 * tautology — a class instance is always truthy — and it was filed as the one genuinely
 * vacuous spec in this tree. It is not. What it asserts is that the root component can be
 * *constructed*, and two mutations on 2026-09-24 separate the cases:
 *
 * - Dropping `RouterOutlet` from `imports` is caught by the **compiler**: `app.html` uses
 *   `<router-outlet>`, so the bundle fails to build and no spec runs at all. That one is
 *   not this test's.
 * - Adding a `readonly x = inject(SOME_TOKEN)` with nothing providing it compiles fine and
 *   throws at construction. That run was **1 failed, 362 passed** — this file, and nothing
 *   else in the suite. No page spec renders `App`; they render their own components.
 *
 * So the assertion is latent rather than empty: the body is `{}` *today*, and this is the
 * only thing standing between an `inject()` added to the root component and a blank page.
 * Delete it when `App` is deleted, not before.
 */
describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });
});
