/// <reference types="vitest/config" />
import { defineConfig } from 'vite'

/**
 * The real-browser checks, run on their own.
 *
 * Two of them share this config and each has its own npm script, because they
 * do not need the same things: the chart walk needs only a browser, while the
 * first-run walk starts a real backend and therefore needs Python. Keeping
 * them apart is what stops the cheaper gate inheriting the heavier one's
 * moving parts.
 *
 * Separate from vite.config.ts because it needs the opposite of everything
 * that one sets up: no jsdom (there is a real browser), no global chart stub
 * (the stub is the thing being bypassed), and no place in the ordinary suite
 * (a Chromium process costs ~300 MB, and on this project's machine the last
 * few GB decide whether a run finishes).
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.browser.test.ts'],
    pool: 'threads',
    // A browser launch, a page load, a drag -- and for the first-run walk, two
    // backend boots and a frontend build. The default 5s is not enough on a
    // cold CI runner, and a timeout there would look like a product bug.
    testTimeout: 180_000,
    hookTimeout: 240_000,
  },
})
