/// <reference types="vitest/config" />
import { defineConfig } from 'vite'

/**
 * The chart's real-browser check, run on its own.
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
    // A browser launch, a page load and a drag. The default 5s is not enough
    // on a cold CI runner, and a timeout there would look like a chart bug.
    testTimeout: 60_000,
    hookTimeout: 60_000,
  },
})
