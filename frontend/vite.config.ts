/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // The one test that drives a REAL browser lives outside this run. It costs
    // a Chromium process (~300 MB) and this project's machine decides whether
    // a suite finishes on its last few GB. `npm run test:chart` runs it, and
    // so does its own CI job.
    exclude: ['**/node_modules/**', '**/dist/**', '**/*.browser.test.ts'],
    globals: true,
    // The default "forks" pool (child_process-based) hangs/times out on
    // this Windows environment, likely related to the working directory
    // path containing spaces. "threads" (worker_threads-based) works.
    pool: 'threads',
  },
})
