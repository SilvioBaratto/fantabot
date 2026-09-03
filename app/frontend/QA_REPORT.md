# Cross-Browser / Device QA Checklist

> **Instructions for the developer:** fill in each TBD field after completing a
> manual pass on real hardware. Do not mark a row ✅ based on assumption — use an
> actual device or BrowserStack session and record the device name + OS version.

## Tested by

| Field           | Value |
|-----------------|-------|
| Tester          | TBD   |
| Date            | TBD   |

---

## Test matrix

Each cell: ✅ pass · ❌ fail · ⚠️ partial · — skipped

### iOS Safari

| Scenario                                     | Mobile | Desktop |
|----------------------------------------------|--------|---------|
| Notch clearance (safe-area-inset-top/bottom) | TBD    | n/a     |
| dvh full-height — toolbar show/hide          | TBD    | n/a     |
| Sticky-footer + safe-area                    | TBD    | n/a     |
| Dark-mode toggle                             | TBD    | TBD     |
| Focus trap (modal, drawer)                   | TBD    | TBD     |

**Device tested:** TBD (e.g. iPhone 15 Pro, iOS 18.4)

---

### Chrome

| Scenario                                     | Mobile | Desktop |
|----------------------------------------------|--------|---------|
| Notch clearance (safe-area-inset-top/bottom) | TBD    | n/a     |
| dvh full-height — toolbar show/hide          | TBD    | n/a     |
| Sticky-footer + safe-area                    | TBD    | TBD     |
| Dark-mode toggle                             | TBD    | TBD     |
| Focus trap (modal, drawer)                   | TBD    | TBD     |

**Device tested:** TBD (e.g. Pixel 8 / Android 15; macOS desktop)

---

### Edge

| Scenario                                     | Mobile | Desktop |
|----------------------------------------------|--------|---------|
| Notch clearance (safe-area-inset-top/bottom) | TBD    | n/a     |
| dvh full-height — toolbar show/hide          | TBD    | n/a     |
| Sticky-footer + safe-area                    | TBD    | TBD     |
| Dark-mode toggle                             | TBD    | TBD     |
| Focus trap (modal, drawer)                   | TBD    | TBD     |

**Device tested:** TBD (e.g. Surface Pro / Windows 11; Android mobile)

---

## Validation areas

### Notch clearance

Verify `pt-safe` on the mobile header and `pb-safe` on the bottom tab-bar clear
the device notch / home-indicator on all tested browsers.

- [ ] Header text/icons are not clipped by the notch
- [ ] Bottom-bar touch targets clear the home indicator

### dvh full-height under toolbar show/hide

Verify the shell uses `h-dvh` and the viewport height tracks correctly when the
browser toolbar slides in/out (iOS Safari address bar, Chrome/Edge compact mode).

- [ ] No content is cut off when toolbar is hidden
- [ ] Layout does not jump when toolbar reappears

### Sticky-footer + safe-area

Verify `position: sticky`/`fixed` elements at the bottom remain visible and
respect `env(safe-area-inset-bottom)` when the toolbar hides.

- [ ] Footer stays above the home indicator
- [ ] No overlap with OS chrome

### Dark-mode toggle

Verify `ThemeService` class toggle activates the `dark:*` utilities in the shell,
shared components, and the `/components` catalog.

- [ ] All surfaces switch to dark tokens
- [ ] No un-themed white flash on toggle

### Focus trap (modal, drawer)

Verify that Tab and Shift-Tab cycle only inside an open modal/drawer, Escape
closes it, and focus returns to the trigger element.

- [ ] Tab wraps at last focusable element
- [ ] Shift-Tab wraps at first focusable element
- [ ] Escape closes and returns focus

---

## Known-fragile patterns to re-check

- Sticky-footer + `env(safe-area-inset-bottom)` when iOS toolbar hides
  (see `research §1.9`)
- `aria-modal` + VoiceOver on recent Safari may not inert background
  (see `research §2.1`)
- `dvh` inside in-app WKWebView browsers (Instagram, Gmail) may behave as `svh`
